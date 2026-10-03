#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

DISTRO="${DISTRO:-ubuntu}"
UBUNTU_REPO="${UBUNTU_REPO:-/root/cumbria-public-records-tracker}"
PROBE_ONLY="${PROBE_ONLY:-0}"
PUBLISH="${PUBLISH:-0}"
STRICT="${STRICT:-0}"
COLLECT_CONFIG="${COLLECT_CONFIG:-config/sources-android-full.yml}"

if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock || true
fi

# This wrapper is convenient from native Termux, but users often leave an
# Ubuntu proot shell open between runs. Detect that case and invoke the inner
# collector directly instead of asking proot-distro to create a nested proot.
if [[ -r /etc/os-release ]] && grep -q '^ID=ubuntu$' /etc/os-release && [[ -d "$UBUNTU_REPO" ]]; then
  cd "$UBUNTU_REPO"
  exec env \
    PROBE_ONLY="$PROBE_ONLY" \
    PUBLISH="$PUBLISH" \
    STRICT="$STRICT" \
    COLLECT_CONFIG="$COLLECT_CONFIG" \
    bash scripts/run-android-browser-ubuntu.sh
fi

exec proot-distro login "$DISTRO" -- bash -lc "
set -euo pipefail
cd '$UBUNTU_REPO'
PROBE_ONLY='$PROBE_ONLY' PUBLISH='$PUBLISH' STRICT='$STRICT' COLLECT_CONFIG='$COLLECT_CONFIG' \
  bash scripts/run-android-browser-ubuntu.sh
"
