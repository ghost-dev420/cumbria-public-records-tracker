#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

DISTRO="${DISTRO:-debian}"
RUNNER_DIR="${RUNNER_DIR:-/root/actions-runner-cumbria}"

if ! command -v proot-distro >/dev/null 2>&1; then
  echo "proot-distro is not installed. Run: pkg install proot-distro"
  exit 2
fi

termux-wake-lock || true

echo "Starting Cumbria browser runner inside $DISTRO."
echo "Leave this Termux session running while GitHub Actions uses the phone."

exec proot-distro login "$DISTRO" -- bash -lc \
  "cd '$RUNNER_DIR' && RUNNER_ALLOW_RUNASROOT=1 ./run.sh"
