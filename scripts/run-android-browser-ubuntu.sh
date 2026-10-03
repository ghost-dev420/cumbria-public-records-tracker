#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/cumbria-public-records-tracker}"
VENV_DIR="${VENV_DIR:-$REPO_DIR/.android-venv}"
PROBE_ONLY="${PROBE_ONLY:-0}"
PUBLISH="${PUBLISH:-0}"

cd "$REPO_DIR"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Android collector environment is not installed."
  echo "Run: bash scripts/setup-android-browser-ubuntu.sh"
  exit 2
fi

# Keep code current without deleting ignored local evidence state.
git fetch origin main
git checkout main
git reset --hard origin/main

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

python -m public_records_tracker.browser_probe \
  'https://cumberland.moderngov.co.uk/mgMemberIndex.aspx?bcr=1' \
  --expect Councillor

python -m public_records_tracker.browser_probe \
  'https://westmorlandandfurness.moderngov.co.uk/mgMemberIndex.aspx?FN=WARD' \
  --expect Councillor

if [[ "$PROBE_ONLY" == "1" ]]; then
  echo "Both ModernGov browser probes passed."
  exit 0
fi

prt init-db
prt collect \
  --config config/sources-moderngov-browser.yml \
  --source all \
  --strict

prt build-site

# Hard publication boundary before anything can leave the phone.
test -d site
test ! -e site/data/raw
test ! -e site/tracker.duckdb
test ! -e site/api/review-queue.json

if find site -type f \( -name '*.duckdb' -o -name '*.duckdb.wal' \) -print -quit | grep -q .; then
  echo "Refusing to publish: database file found under site/."
  exit 1
fi

if find site -type f -path '*/data/raw/*' -print -quit | grep -q .; then
  echo "Refusing to publish: raw evidence path found under site/."
  exit 1
fi

echo "Publication boundary checks passed."

if [[ "$PUBLISH" == "1" ]]; then
  bash scripts/publish-android-site.sh
else
  echo "Collection complete. Local private state remains under data/."
  echo "To publish the sanitized site: PUBLISH=1 bash scripts/run-android-browser-ubuntu.sh"
fi
