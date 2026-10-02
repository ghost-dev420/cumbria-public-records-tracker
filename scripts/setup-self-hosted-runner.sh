#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/ghost-dev420/cumbria-public-records-tracker"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner-cumbria}"
RUNNER_NAME="${RUNNER_NAME:-cumbria-browser-$(hostname)}"
RUNNER_LABELS="${RUNNER_LABELS:-cumbria-browser}"

if [[ -z "${RUNNER_TOKEN:-}" ]]; then
  echo "RUNNER_TOKEN is required."
  echo "GitHub: repo Settings -> Actions -> Runners -> New self-hosted runner"
  echo "Then run: RUNNER_TOKEN='<token>' bash scripts/setup-self-hosted-runner.sh"
  exit 2
fi

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
  echo "This bootstrap currently supports Linux x86_64 only."
  exit 2
fi

sudo apt-get update
sudo apt-get install -y curl jq python3 python3-venv ca-certificates

latest_tag="$({ curl -fsSL https://api.github.com/repos/actions/runner/releases/latest || true; } | jq -r '.tag_name // empty')"
if [[ -z "$latest_tag" ]]; then
  echo "Could not determine the latest GitHub Actions runner release."
  exit 3
fi
version="${latest_tag#v}"
archive="actions-runner-linux-x64-${version}.tar.gz"
url="https://github.com/actions/runner/releases/download/${latest_tag}/${archive}"

mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"
if [[ ! -x ./config.sh ]]; then
  curl -fL "$url" -o "$archive"
  tar xzf "$archive"
  rm -f "$archive"
fi

# Install GitHub runner runtime dependencies.
sudo ./bin/installdependencies.sh

# Install Chromium OS dependencies once. Browser binaries themselves are
# installed/cached by the repository workflow under the runner account.
python3 -m venv .playwright-setup
.playwright-setup/bin/pip install --upgrade pip playwright
sudo .playwright-setup/bin/python -m playwright install-deps chromium
rm -rf .playwright-setup

./config.sh \
  --url "$REPO_URL" \
  --token "$RUNNER_TOKEN" \
  --name "$RUNNER_NAME" \
  --labels "$RUNNER_LABELS" \
  --work _work \
  --unattended \
  --replace

sudo ./svc.sh install "$USER"
sudo ./svc.sh start

echo
echo "Runner configured: $RUNNER_NAME"
echo "Required custom label: $RUNNER_LABELS"
echo "The workflow 'collect ModernGov on self-hosted browser' can now be run manually."
