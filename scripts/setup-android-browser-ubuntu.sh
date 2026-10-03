#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/cumbria-public-records-tracker}"
VENV_DIR="${VENV_DIR:-$REPO_DIR/.android-venv}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This setup must run inside the Ubuntu proot environment."
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  ca-certificates \
  curl \
  git \
  python3 \
  python3-pip \
  python3-venv

# GitHub CLI is used only to authenticate the optional sanitized-site push.
# Collection itself does not need GitHub credentials.
if ! command -v gh >/dev/null 2>&1; then
  if ! apt-get install -y gh; then
    echo "Warning: GitHub CLI could not be installed from this Ubuntu release."
    echo "Collection will still work; publishing can be configured later."
  fi
fi

cd "$REPO_DIR"
python3 -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
pip install -e .

prt init-db

cat <<'EOF'

Android ModernGov XML collector is installed.

Quick access test (no full crawl):
  PROBE_ONLY=1 bash scripts/run-android-browser-ubuntu.sh

Full local collection:
  bash scripts/run-android-browser-ubuntu.sh

To publish the publication-gated site, authenticate GitHub once:
  gh auth login --hostname github.com --git-protocol https --web
  gh auth setup-git

Then run:
  PUBLISH=1 bash scripts/run-android-browser-ubuntu.sh

The DuckDB and data/raw remain local to this Ubuntu proot. Only site/ is copied to the publish branch.
EOF
