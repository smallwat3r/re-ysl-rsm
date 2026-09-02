#!/usr/bin/env bash
# Pull the two decaying artifacts off your partner's Android phone: the APK and
# a BLE capture of the working app. Needs `adb` and the phone in USB debugging.
#
#   ./phone.sh apk       -> pulls every APK split into ./apk/
#   ./phone.sh capture   -> pulls a bugreport and extracts btsnoop_hci.log
#
# Before `capture`: on the phone enable Developer Options -> "Enable Bluetooth
# HCI snoop log", then do ONE clean session (connect, cartridge recognition,
# prime, several dispenses at deliberately different shades) and come back here.
set -euo pipefail

PKG=com.loreal.ysl.perso.lips
cmd=${1:-}

case "$cmd" in
apk)
  mkdir -p apk
  echo "Locating $PKG ..."
  # A modern install is split across several APKs; pull them all.
  adb shell pm path "$PKG" | sed 's/package://' | tr -d '\r' | while read -r p; do
    echo "  pulling $p"
    adb pull "$p" "apk/$(basename "$p")"
  done
  echo "Done. Decompile with: jadx-gui apk/base.apk"
  ;;

capture)
  echo "Pulling bugreport (this takes a minute)..."
  adb bugreport bugreport.zip
  # btsnoop path moved around across Android versions; grab whatever matches.
  snoop=$(unzip -Z1 bugreport.zip | grep -i 'btsnoop' | head -n1 || true)
  if [ -z "$snoop" ]; then
    echo "No btsnoop in the zip. Check the snoop-log toggle was on, then redo the session." >&2
    exit 1
  fi
  unzip -o -j bugreport.zip "$snoop" -d .
  echo "Extracted $(basename "$snoop"). Open in Wireshark, filter to the device BD_ADDR,"
  echo "and every ATT 'Write Request' value is a command the device accepts."
  ;;

*)
  echo "usage: $0 {apk|capture}" >&2
  exit 2
  ;;
esac
