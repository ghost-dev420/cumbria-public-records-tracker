#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/ghost-dev420/cumbria-public-records-tracker"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner-cumbria}"
RUNNER_NAME="${RUNNER_NAME:-cumbria-browser-$(hostname)}"
RUNNER_LABELS="${RUNNER_LABELS:-cumbria-browser}"
RUNNER_NO_SERVICE="${RUNNER_NO_SERVICE:-0}"

if [[ -z "${RUNNER_TOKEN:-}" ]]; then
  echo "RUNNER_TOKEN is required."
  echo "GitHub: repo Settings -> Actions -> Runners -> New self-hosted runner"
  echo "Then run: RUNNER_TOKEN='<token>' bash scripts/setup-self-hosted-runner.sh"
  exit 2
fi

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This bootstrap requires a Linux userspace."
  echo "On Android/Termux, enter a Debian or Ubuntu proot first."
  exit 2
fi

case "$(uname -m)" in
  x86_64|amd64)
    runner_arch="x64"
    ;;
  aarch64|arm64)
    runner_arch="arm64"
    ;;
  *)
    echo "Unsupported architecture: $(uname -m). Supported: x86_64, aarch64/arm64."
    exit 2
    ;;
esac

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  SUDO=""
  export RUNNER_ALLOW_RUNASROOT=1
elif command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
else
  echo "Need root privileges (or sudo) to install Linux/Chromium dependencies."
  exit 2
fi

$SUDO apt-get update
$SUDO apt-get install -y \
  curl jq git python3 python3-venv python3-pip ca-certificates

latest_tag="$({ curl -fsSL https://api.github.com/repos/actions/runner/releases/latest || true; } | jq -r '.tag_name // empty')"
if [[ -z "$latest_tag" ]]; then
  echo "Could not determine the latest GitHub Actions runner release."
  exit 3
fi
version="${latest_tag#v}"
archive="actions-runner-linux-${runner_arch}-${version}.tar.gz"
url="https://github.com/actions/runner/releases/download/${latest_tag}/${archive}"

mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"
if [[ ! -x ./config.sh ]]; then
  curl -fL "$url" -o "$archive"
  tar xzf "$archive"
  rm -f "$archive"
fi

# Install GitHub runner runtime dependencies.
$SUDO ./bin/installdependencies.sh

# Install Chromium OS dependencies once. Browser binaries themselves are
# installed/cached by the repository workflow under the runner account.
python3 -m venv .playwright-setup
.playwright-setup/bin/pip install --upgrade pip playwright
$SUDO .playwright-setup/bin/python -m playwright install-deps chromium
rm -rf .playwright-setup

./config.sh \
  --url "$REPO_URL" \
  --token "$RUNNER_TOKEN" \
  --name "$RUNNER_NAME" \
  --labels "$RUNNER_LABELS" \
  --work _work \
  --unattended \
  --replace

echo
if [[ "$RUNNER_NO_SERVICE" == "1" ]]; then
  echo "Runner configured in foreground mode: $RUNNER_NAME"
  echo "Start it with:"
  echo "  cd '$RUNNER_DIR' && RUNNER_ALLOW_RUNASROOT=1 ./run.sh"
elif command -v systemctl >/dev/null 2>&1 && [[ -x ./svc.sh ]]; then
  $SUDO ./svc.sh install "${SUDO_USER:-$USER}"
  $SUDO ./svc.sh start
  echo "Runner configured and service started: $RUNNER_NAME"
else
  echo "No usable service manager detected. Runner configured: $RUNNER_NAME"
  echo "Start it in the foreground with:"
  echo "  cd '$RUNNER_DIR' && RUNNER_ALLOW_RUNASROOT=1 ./run.sh"
fi

echo "Required custom label: $RUNNER_LABELS"
echo "The workflow 'collect ModernGov on self-hosted browser' can now be run manually."
