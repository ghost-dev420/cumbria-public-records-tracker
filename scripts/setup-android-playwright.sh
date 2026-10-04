#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/cumbria-public-records-tracker}"
VENV_DIR="${VENV_DIR:-$REPO_DIR/.android-venv}"

cd "$REPO_DIR"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Android collector environment is not installed."
  echo "Run: bash scripts/setup-android-browser-ubuntu.sh"
  exit 2
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Install the optional Python browser transport, then let Playwright install its
# supported Chromium build plus the required Ubuntu/Debian shared libraries.
python -m pip install -q -e '.[browser]'
python -m playwright install --with-deps chromium

python - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content("<title>ok</title><p>browser runtime ready</p>")
    assert page.title() == "ok"
    browser.close()

print("Android Playwright Chromium runtime is ready.")
PY
