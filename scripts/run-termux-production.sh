#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

# Production must run where the persistent DuckDB and raw archive live.
# This is a native Termux launcher; GitHub-hosted runners cannot access them.
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
export PATH="$PREFIX/bin:/system/bin:${PATH:-}"
DISTRO="${DISTRO:-ubuntu}"
UBUNTU_REPO="${UBUNTU_REPO:-/root/cumbria-public-records-tracker}"
PUBLISH="${PUBLISH:-1}"

if ! command -v proot-distro >/dev/null 2>&1; then
  echo "Run this in native Termux with proot-distro installed." >&2
  exit 2
fi

# An Android wake lock reduces the chance of suspension mid-collection.
if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock || true
fi

# All modifications to data/tracker.duckdb happen inside update-production.sh,
# which holds an exclusive production lock and backs up the DB first.
# Fast-forward only: never reset local commits or delete ignored evidence.
printf -v run_command 'set -Eeuo pipefail; cd %q; git fetch origin main; git checkout main; git merge --ff-only origin/main; test -f data/tracker.duckdb || { echo "Missing persistent DB. Run the Android setup first." >&2; exit 2; }; test -x .android-venv/bin/python || { echo "Missing Android venv. Run the Android setup first." >&2; exit 2; }; source .android-venv/bin/activate; python -m pip install -q -e %q; REPO_DIR=%q PUBLISH=%q bash scripts/update-production.sh' \
  "$UBUNTU_REPO" '.[browser]' "$UBUNTU_REPO" "$PUBLISH"

exec proot-distro login "$DISTRO" -- bash -lc "$run_command"
