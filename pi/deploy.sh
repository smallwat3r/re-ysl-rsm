#!/usr/bin/env bash
# Push this checkout to a Pi over SSH and restart the app there. Ships the
# git-tracked files as they are in the working tree (uncommitted edits count,
# new files need git add first) plus .env. SETUP=1 also reruns pi/setup.sh on
# the Pi: do that after changing .env, pi/ or requirements.txt. Needs
# passwordless sudo on the Pi (the Raspberry Pi OS default).
#
#   make deploy                    # host from RSM_PI in .env
#   make deploy PI=pi@10.42.0.1    # over the hotspot
#   make deploy SETUP=1
#
# Works over the hotspot, the home network or an ethernet cable. On a cable the
# Pi Zero has no address until the laptop hands one out, once:
#   nmcli connection modify "Wired connection 1" ipv4.method shared
# then `ip neigh` on the laptop shows the Pi's lease.
set -euo pipefail

main() {
  preflight "$@"
  push
  restart
}

step() { printf '\n== %s\n' "$*"; }
die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

# Move to the repo root, load .env, and work out where to deploy to.
preflight() {
  cd "$(dirname "$0")/.."
  [[ -f .env ]] || die "no .env, copy .env.example and fill it in"
  # shellcheck disable=SC1091
  . ./.env
  host=${1:-${RSM_PI:-}}
  [[ -n $host ]] || die "usage: pi/deploy.sh user@host   (or set RSM_PI in .env)"
  dir=${RSM_PI_DIR:-re-ysl-rsm} # the checkout on the Pi, relative to its home
}

push() {
  step "copying to $host:$dir"
  # -m: the Pi's clock lags without internet, keep tar quiet about mtimes
  git ls-files -z | tar -c --null -T - .env | ssh "$host" "mkdir -p '$dir' && tar -xm -C '$dir'"
}

restart() {
  step "restarting"
  ssh "$host" bash -s "$dir" "${SETUP:-0}" <<'REMOTE'
set -euo pipefail
cd "$1"
if [[ $2 == 1 ]]; then ./pi/setup.sh; fi
sudo systemctl restart rsm
! systemctl -q is-enabled rsm-portal 2>/dev/null || sudo systemctl restart rsm-portal
systemctl --no-pager --lines=0 status rsm | sed -n '3p'
REMOTE
}

main "$@"
