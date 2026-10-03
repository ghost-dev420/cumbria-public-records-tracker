#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/cumbria-public-records-tracker}"
VENV_DIR="${VENV_DIR:-$REPO_DIR/.android-venv}"
PROBE_ONLY="${PROBE_ONLY:-0}"
PUBLISH="${PUBLISH:-0}"
STRICT="${STRICT:-0}"
COLLECT_CONFIG="${COLLECT_CONFIG:-config/sources-android-full.yml}"

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

# Pull in any dependency changes that landed with the code update. Pip is
# idempotent here, so already-satisfied packages are reused from the venv.
python -m pip install -q -e .

python -m public_records_tracker.modern_gov_api_probe \
  'https://cumberland.moderngov.co.uk' \
  --operation GetCommittees \
  --expect-tag committees

python -m public_records_tracker.modern_gov_api_probe \
  'https://westmorlandandfurness.moderngov.co.uk' \
  --operation GetCommittees \
  --expect-tag committees

if [[ "$PROBE_ONLY" == "1" ]]; then
  echo "Both ModernGov XML API probes passed."
  exit 0
fi

prt init-db
collect_args=(
  --config "$COLLECT_CONFIG"
  --source all
)
if [[ "$STRICT" == "1" ]]; then
  collect_args+=(--strict)
fi

printf 'Running full production collection with %s\n' "$COLLECT_CONFIG"
prt collect "${collect_args[@]}"

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
