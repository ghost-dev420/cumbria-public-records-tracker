#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

REPO_URL="https://github.com/ghost-dev420/cumbria-public-records-tracker.git"
DISTRO="${DISTRO:-ubuntu}"
RUNNER_NAME="${RUNNER_NAME:-cumbria-android-$(getprop ro.product.model 2>/dev/null | tr ' ' '-' || echo termux)}"
RUNNER_LABELS="${RUNNER_LABELS:-cumbria-browser,android-termux}"

if [[ -z "${RUNNER_TOKEN:-}" ]]; then
  echo "RUNNER_TOKEN is required."
  echo "GitHub: repo Settings -> Actions -> Runners -> New self-hosted runner -> Linux -> ARM64"
  echo "Then run: RUNNER_TOKEN='<token>' bash scripts/setup-termux-arm64-runner.sh"
  exit 2
fi

case "$(uname -m)" in
  aarch64|arm64) ;;
  *)
    echo "This bootstrap is for ARM64 Android devices. Detected: $(uname -m)"
    exit 2
    ;;
esac

pkg update -y
pkg install -y proot-distro git tmux

if ! proot-distro list | grep -q "${DISTRO}"; then
  proot-distro install "$DISTRO"
fi

# Keep Android from suspending Termux while the runner is expected to be online.
if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock || true
fi

proot-distro login "$DISTRO" -- bash -lc "
set -euo pipefail
apt-get update
apt-get install -y git ca-certificates curl
if [[ ! -d /root/cumbria-public-records-tracker/.git ]]; then
  git clone '$REPO_URL' /root/cumbria-public-records-tracker
else
  git -C /root/cumbria-public-records-tracker fetch origin
  git -C /root/cumbria-public-records-tracker reset --hard origin/main
fi
cd /root/cumbria-public-records-tracker
RUNNER_TOKEN='$RUNNER_TOKEN' \
RUNNER_NAME='$RUNNER_NAME' \
RUNNER_LABELS='$RUNNER_LABELS' \
RUNNER_NO_SERVICE=1 \
bash scripts/setup-self-hosted-runner.sh
"

cat <<'EOF'

Android ARM64 runner configured.

Start it in the foreground with:
  bash scripts/start-termux-arm64-runner.sh

Or keep it in a tmux session:
  tmux new -s cumbria-runner 'bash scripts/start-termux-arm64-runner.sh'

Detach from tmux with Ctrl-b then d.
Android may still kill Termux under aggressive battery optimisation, so exempt Termux
from battery optimisation if you want scheduled collection to run reliably.
EOF