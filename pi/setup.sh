#!/usr/bin/env bash
# Set up a Raspberry Pi as a controller: Bluetooth on at boot, the web app as a
# systemd service, the journal in RAM to spare the SD card, and, when
# RSM_HOTSPOT_SSID is set in .env, a Wi-Fi hotspot the phone or laptop joins to
# reach the page plus a captive-portal redirect. Without it the Pi stays on the
# home Wi-Fi and serves the page there. Idempotent, rerun it after changing
# .env. Runs as your user and calls sudo where needed.
#
#   cp .env.example .env   # RSM_ADDRESS (make scan), RSM_LAN=1, optional hotspot SSID/password
#   pi/setup.sh
#   make pair              # once, with the device awake (bonds are per machine)
#   sudo reboot            # everything comes up on its own from now on
#
# The Pi has one radio, so while the hotspot is up it is off your home Wi-Fi:
# reach it through the hotspot (ssh pi@10.42.0.1). To get it back online for a
# git pull see pi/hotspot/NetworkManager.conf. A reboot brings the hotspot back.
set -euo pipefail

UNITS=(rsm.service) # plus rsm-portal.service when the hotspot is configured

main() {
  preflight
  install_packages
  enable_bluetooth
  journal_in_ram
  skip_cloud_init
  install_services
  if [[ -n ${RSM_HOTSPOT_SSID:-} ]]; then
    configure_hotspot
  else
    remove_hotspot
  fi
  printf '\ndone. Next: make pair (device awake), then sudo reboot.\n'
}

step() { printf '\n== %s\n' "$*"; }
die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

# install_file <template under pi/> <destination> [mode]
# The templates carry the checkout path and user of a stock Pi, and __SSID__ /
# __PASSWORD__ for the hotspot, replaced here with the real values.
install_file() {
  local content
  content=$(<"pi/$1")
  content=${content//\/home\/pi\/re-ysl-rsm/$PWD}
  content=${content//User=pi/User=$USER}
  content=${content//__SSID__/${RSM_HOTSPOT_SSID:-}}
  content=${content//__PASSWORD__/${RSM_HOTSPOT_PASSWORD:-}}
  sudo install -D -m "${3:-644}" /dev/null "$2"
  printf '%s\n' "$content" | sudo tee "$2" >/dev/null
}

# Move to the repo root, load .env, and die with a hint on the first problem.
preflight() {
  cd "$(dirname "$0")/.."
  [[ -f .env ]] || die "no .env, copy .env.example and fill it in"
  # shellcheck disable=SC1091
  . ./.env

  # required settings
  [[ -n ${RSM_ADDRESS:-} && ${RSM_ADDRESS} != AA:BB:CC:DD:EE:FF ]] || die "set RSM_ADDRESS in .env (find it with make scan)"
  [[ ${RSM_LAN:-} == 1 ]] || die "set RSM_LAN=1 in .env so other devices can reach the page"
  [[ -z ${RSM_HOSTS:-} ]] || die "RSM_HOSTS is no longer used, replace it with RSM_LAN=1 in .env"

  # optional hotspot: an SSID in .env turns it on, and adds the portal unit
  : "${RSM_HOTSPOT_PASSWORD:=}"
  if [[ -n ${RSM_HOTSPOT_SSID:-} ]]; then
    [[ ${#RSM_HOTSPOT_PASSWORD} -ge 8 ]] || die "RSM_HOTSPOT_PASSWORD in .env must be 8+ characters (WPA2)"
    command -v nmcli >/dev/null || die "NetworkManager not found, this needs Raspberry Pi OS Bookworm or newer"
    [[ -d /sys/class/net/wlan0 ]] || die "no wlan0 interface"
    UNITS+=(rsm-portal.service)
  fi

  sudo -n true 2>/dev/null || sudo -v # ask for the sudo password once, up front
}

# apt_install <package>...: only when one is missing, so reruns work offline
# (over the hotspot or a cable) and never pull upgrades.
apt_install() {
  dpkg -s "$@" >/dev/null 2>&1 && return
  sudo apt-get update -q
  sudo apt-get install -y -q "$@"
}

install_packages() {
  step "packages"
  apt_install python3-venv
}

enable_bluetooth() {
  step "bluetooth on at boot"
  sudo rfkill unblock bluetooth
  if grep -q '^#\?AutoEnable=' /etc/bluetooth/main.conf; then
    sudo sed -i 's/^#\?AutoEnable=.*/AutoEnable=true/' /etc/bluetooth/main.conf
  else
    printf '\n[Policy]\nAutoEnable=true\n' | sudo tee -a /etc/bluetooth/main.conf >/dev/null
  fi
  sudo systemctl enable bluetooth >/dev/null
  sudo systemctl restart bluetooth
}

journal_in_ram() {
  step "journal in RAM (no log writes to the SD card)"
  # The app logs every BLE frame, the only steady write load on the card. Keep
  # journald in tmpfs: journalctl and the page still show everything for the
  # current boot, history across reboots is dropped.
  sudo install -d /etc/systemd/journald.conf.d
  printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=16M\n' |
    sudo tee /etc/systemd/journald.conf.d/rsm-volatile.conf >/dev/null
  sudo systemctl restart systemd-journald
}

skip_cloud_init() {
  step "cloud-init off (first-boot setup is done, it costs ~25s every boot)"
  # Raspberry Pi OS runs cloud-init to apply the Imager settings (user,
  # hostname, Wi-Fi) on first boot only, after that it just slows the boot down.
  # This marker file is its documented off switch.
  sudo touch /etc/cloud/cloud-init.disabled
}

install_services() {
  step "services: ${UNITS[*]}"
  make venv
  local unit
  for unit in "${UNITS[@]}"; do
    install_file "$unit" "/etc/systemd/system/$unit"
  done
  sudo systemctl daemon-reload
  sudo systemctl enable "${UNITS[@]}" >/dev/null
}

portal_cert() {
  # Self-signed cert so https://10.42.0.1 redirects too (after the browser's
  # one-time warning). Combined key+cert PEM read by pi/portal.py.
  [[ -f pi/portal.pem ]] && return
  step "self-signed certificate for https://10.42.0.1"
  local tmp
  tmp=$(mktemp -d)
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=10.42.0.1" \
    -addext "subjectAltName=IP:10.42.0.1" -keyout "$tmp/key" -out "$tmp/crt" 2>/dev/null
  cat "$tmp/key" "$tmp/crt" >pi/portal.pem
  chmod 600 pi/portal.pem
  rm -rf "$tmp"
}

configure_hotspot() {
  step "hotspot $RSM_HOTSPOT_SSID on 10.42.0.1 (hostapd + dnsmasq)"
  apt_install hostapd dnsmasq openssl
  sudo nmcli connection delete rsm-hotspot >/dev/null 2>&1 || true # earlier versions
  install_file hotspot/NetworkManager.conf /etc/NetworkManager/conf.d/rsm-hotspot.conf
  install_file hotspot/hostapd.conf /etc/hostapd/hostapd.conf 600
  install_file hotspot/hostapd.service /etc/systemd/system/hostapd.service
  install_file hotspot/dnsmasq.conf /etc/dnsmasq.d/rsm-hotspot.conf
  install_file hotspot/dnsmasq.service /etc/systemd/system/dnsmasq.service
  sudo nmcli general reload conf
  sudo nmcli device set wlan0 managed no
  sudo systemctl unmask hostapd >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable hostapd dnsmasq >/dev/null
  sudo systemctl restart dnsmasq hostapd
  portal_cert
}

remove_hotspot() {
  step "no RSM_HOTSPOT_SSID in .env, no hotspot (page served on the home Wi-Fi)"
  # a hotspot from an earlier run would otherwise keep coming up at boot
  sudo systemctl disable --now hostapd dnsmasq >/dev/null 2>&1 || true
  sudo rm -f /etc/NetworkManager/conf.d/rsm-hotspot.conf /etc/hostapd/hostapd.conf \
    /etc/systemd/system/hostapd.service /etc/systemd/system/dnsmasq.service /etc/dnsmasq.d/rsm-hotspot.conf
  sudo nmcli connection delete rsm-hotspot >/dev/null 2>&1 || true # earlier versions
  sudo nmcli general reload conf
  sudo nmcli device set wlan0 managed yes >/dev/null 2>&1 || true
}

main "$@"
