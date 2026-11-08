#!/usr/bin/env bash
set -Eeuo pipefail

# Fail closed: do not overwrite Pages with a failed/empty or unsanitized build.
SITE="${1:-site}"
test -s "$SITE/index.html"
test -s "$SITE/findings.html"
test -s "$SITE/api/index.json"
test -s "$SITE/api/findings.json"
test -s "$SITE/api/source-health.json"
test ! -e "$SITE/data/raw"
test ! -e "$SITE/tracker.duckdb"
test ! -e "$SITE/api/review-queue.json"

if find "$SITE" -type f \( -name '*.duckdb' -o -name '*.duckdb.wal' -o -name '*.sqlite' -o -name '*.db' \) -print -quit | grep -q .; then
  echo 'Private database found in public site; refusing deployment.' >&2
  exit 1
fi
if find "$SITE" -type f -path '*/data/raw/*' -print -quit | grep -q .; then
  echo 'Private raw evidence path found in public site; refusing deployment.' >&2
  exit 1
fi

python - "$SITE" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
api = root / 'api'
index = json.loads((api / 'index.json').read_text(encoding='utf-8'))
endpoints = index.get('endpoints') or {}
if 'review-queue' in endpoints:
    raise SystemExit('Review-queue endpoint in public API index')
for name, allowed in [('signals', {'approved'}), ('matches', {'accepted', 'auto_accepted'})]:
    path = api / f'{name}.json'
    if not path.exists():
        continue
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, list):
        raise SystemExit(f'Unexpected {name} payload type')
    for item in payload:
        if item.get('status') not in allowed:
            raise SystemExit(f'Unsafe {name} status published: {item.get("status")!r}')
for entry in root.rglob('*'):
    if not entry.is_symlink():
        continue
    raise SystemExit(f'Symlink within public site: {entry}')
print('Publication boundary passed: approved signals, accepted matches, no private state.')
PY
