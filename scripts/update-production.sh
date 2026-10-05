#!/usr/bin/env bash
set -Eeuo pipefail

REPO_DIR="${REPO_DIR:-/root/cumbria-public-records-tracker}"
DB="${DB:-$REPO_DIR/data/tracker.duckdb}"
ARCHIVE="${ARCHIVE:-$REPO_DIR/data/raw}"
CONFIG="${CONFIG:-$REPO_DIR/config/sources-android-full.yml}"
LOG_DIR="${LOG_DIR:-$REPO_DIR/data/logs}"
BACKUP_DIR="${BACKUP_DIR:-$REPO_DIR/data/backups}"
REFERENCE_CACHE="${REFERENCE_CACHE:-$REPO_DIR/data/reference-cache}"
PUBLISH="${PUBLISH:-1}"
ENABLE_REFERENCE_REFRESH="${ENABLE_REFERENCE_REFRESH:-1}"
ENABLE_PSC_REFRESH="${ENABLE_PSC_REFRESH:-1}"
REFERENCE_REFRESH_DAYS="${REFERENCE_REFRESH_DAYS:-14}"
PSC_REFRESH_DAYS="${PSC_REFRESH_DAYS:-30}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

mkdir -p "$LOG_DIR" "$BACKUP_DIR" "$REFERENCE_CACHE"
cd "$REPO_DIR"

# Do not allow a cron run, a manual run and a self-hosted Actions run to mutate
# the persistent DuckDB at the same time.
LOCK_FILE="$REPO_DIR/data/.production-update.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Another production update is already running; exiting cleanly."
  exit 0
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/production-$stamp.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Cumbria public-records production update: $stamp ==="

if [[ -f "$REPO_DIR/.android-venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$REPO_DIR/.android-venv/bin/activate"
fi

if ! command -v prt >/dev/null 2>&1; then
  echo "prt is not installed in the active environment."
  exit 2
fi

if [[ ! -f "$DB" ]]; then
  echo "Persistent database not found: $DB"
  exit 2
fi

backup="$BACKUP_DIR/tracker-$stamp.duckdb"
cp -a "$DB" "$backup"
echo "Backup: $backup"
find "$BACKUP_DIR" -type f -name 'tracker-*.duckdb' -mtime "+$BACKUP_RETENTION_DAYS" -delete || true

_due() {
  local stamp_file="$1"
  local days="$2"
  [[ ! -f "$stamp_file" ]] && return 0
  find "$stamp_file" -mtime "+$((days - 1))" -print -quit | grep -q .
}

basic_stamp="$REFERENCE_CACHE/.basic-last-success"
psc_stamp="$REFERENCE_CACHE/.psc-last-success"

# Registry snapshots are much larger than ordinary council sources, so refresh
# them on a slower cadence. Failure does not stop council collection/publication;
# it is recorded in this production log and the previous reference index remains.
# Refresh registry data on an isolated database copy.  Bulk registry imports
# are large and depend on third-party CSV/ZIP files; an importer or DuckDB failure
# must never leave the production database partially mutated.
_refresh_reference_safely() {
  local psc_flag="$1"
  local refresh_db="$BACKUP_DIR/reference-refresh-$stamp.duckdb"

  rm -f "$refresh_db" "$refresh_db.wal"
  cp -a "$DB" "$refresh_db"

  if prt refresh-reference-index --db "$refresh_db" --cache-dir "$REFERENCE_CACHE" "$psc_flag"; then
    # Force a fresh process to open the completed copy before it can replace the
    # production DB.  This catches invalidated/corrupt files after the importer
    # process has exited.
    if python - "$refresh_db" <<'PY'
import sys
import duckdb

con = duckdb.connect(sys.argv[1], read_only=True)
con.execute("SELECT 1").fetchone()
con.close()
PY
    then
      mv -f "$refresh_db" "$DB"
      rm -f "$refresh_db.wal"
      return 0
    fi
    echo "WARN: refreshed reference database failed validation; discarding it."
  fi

  rm -f "$refresh_db" "$refresh_db.wal"
  return 1
}

if [[ "$ENABLE_REFERENCE_REFRESH" == "1" ]]; then
  if [[ "$ENABLE_PSC_REFRESH" == "1" ]] && _due "$psc_stamp" "$PSC_REFRESH_DAYS"; then
    echo "Refreshing Companies House/Charity reference index including PSC data (isolated copy)..."
    if _refresh_reference_safely --psc; then
      touch "$psc_stamp" "$basic_stamp"
    else
      echo "WARN: PSC/reference refresh failed; production database was not modified."
    fi
  elif _due "$basic_stamp" "$REFERENCE_REFRESH_DAYS"; then
    echo "Refreshing Companies House/Charity reference index (isolated copy)..."
    if _refresh_reference_safely --no-psc; then
      touch "$basic_stamp"
    else
      echo "WARN: reference refresh failed; production database was not modified."
    fi
  fi
fi

echo "Collecting configured public-record sources..."
prt collect \
  --source all \
  --config "$CONFIG" \
  --db "$DB" \
  --archive "$ARCHIVE"

echo "Running final consolidated analysis, including PSC/supplier connections..."
python "$REPO_DIR/scripts/reanalyze.py" --db "$DB" --no-backup

echo "Building sanitized public site..."
rm -rf "$REPO_DIR/site"
prt build-site --db "$DB" --out "$REPO_DIR/site"

test -f "$REPO_DIR/site/index.html"
test -f "$REPO_DIR/site/findings.html"
test -f "$REPO_DIR/site/api/findings.json"
test ! -e "$REPO_DIR/site/api/review-queue.json"
test ! -e "$REPO_DIR/site/tracker.duckdb"

if [[ "$PUBLISH" == "1" ]]; then
  echo "Publishing sanitized site..."
  bash "$REPO_DIR/scripts/publish-android-site.sh"
else
  echo "PUBLISH=$PUBLISH; leaving the sanitized site locally without pushing."
fi

echo "=== Production update complete ==="
echo "Log: $LOG_FILE"
