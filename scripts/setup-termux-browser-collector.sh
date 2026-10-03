#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/ghost-dev420/cumbria-public-records-tracker.git}"
DISTRO="${DISTRO:-ubuntu}"
UBUNTU_REPO="${UBUNTU_REPO:-/root/cumbria-public-records-tracker}"

case "$(uname -m)" in
  aarch64|arm64) ;;
  *)
    echo "This setup targets ARM64 Android. Detected: $(uname -m)"
    exit 2
    ;;
esac

pkg update -y
pkg install -y proot-distro git tmux

if ! proot-distro login "$DISTRO" -- true >/dev/null 2>&1; then
  proot-distro install "$DISTRO"
fi

if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock || true
fi

proot-distro login "$DISTRO" -- bash -lc "
set -euo pipefail
apt-get update
apt-get install -y git ca-certificates curl
if [[ ! -d '$UBUNTU_REPO/.git' ]]; then
  git clone '$REPO_URL' '$UBUNTU_REPO'
else
  git -C '$UBUNTU_REPO' fetch origin main
  git -C '$UBUNTU_REPO' checkout main
  git -C '$UBUNTU_REPO' reset --hard origin/main
fi
cd '$UBUNTU_REPO'
bash scripts/setup-android-browser-ubuntu.sh
"

cat <<'EOF'

Direct Android browser collector configured. No GitHub Actions runner or CoreCLR is used.

Test browser access only:
  PROBE_ONLY=1 bash scripts/run-termux-browser-collector.sh

Run a full local collection:
  bash scripts/run-termux-browser-collector.sh

After GitHub CLI authentication inside Ubuntu, publish only the sanitized site with:
  PUBLISH=1 bash scripts/run-termux-browser-collector.sh
EOF
