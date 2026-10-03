#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

DISTRO="${DISTRO:-ubuntu}"
UBUNTU_REPO="${UBUNTU_REPO:-/root/cumbria-public-records-tracker}"
PROBE_ONLY="${PROBE_ONLY:-0}"
PUBLISH="${PUBLISH:-0}"

if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock || true
fi

exec proot-distro login "$DISTRO" -- bash -lc "
set -euo pipefail
cd '$UBUNTU_REPO'
PROBE_ONLY='$PROBE_ONLY' PUBLISH='$PUBLISH' bash scripts/run-android-browser-ubuntu.sh
"
