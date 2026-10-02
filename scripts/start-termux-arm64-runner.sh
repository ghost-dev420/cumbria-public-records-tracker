#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

DISTRO="${DISTRO:-ubuntu}"
RUNNER_DIR="${RUNNER_DIR:-/root/actions-runner-cumbria}"

if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock || true
fi

exec proot-distro login "$DISTRO" -- bash -lc "
set -euo pipefail
cd '$RUNNER_DIR'
export RUNNER_ALLOW_RUNASROOT=1
exec ./run.sh
"
