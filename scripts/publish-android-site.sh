#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/cumbria-public-records-tracker}"
REPO_URL="${REPO_URL:-https://github.com/ghost-dev420/cumbria-public-records-tracker.git}"
PUBLISH_BRANCH="${PUBLISH_BRANCH:-android-publish}"
SITE_DIR="${SITE_DIR:-$REPO_DIR/site}"

if [[ ! -d "$SITE_DIR" ]]; then
  echo "No site/ directory found. Run the collector first."
  exit 2
fi

# Repeat the privacy boundary here so this script is safe to call directly.
test ! -e "$SITE_DIR/data/raw"
test ! -e "$SITE_DIR/tracker.duckdb"
test ! -e "$SITE_DIR/api/review-queue.json"

if find "$SITE_DIR" -type f \( -name '*.duckdb' -o -name '*.duckdb.wal' \) -print -quit | grep -q .; then
  echo "Refusing to publish: database file found in site/."
  exit 1
fi

if find "$SITE_DIR" -type f -path '*/data/raw/*' -print -quit | grep -q .; then
  echo "Refusing to publish: raw evidence path found in site/."
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI is required for authenticated publishing."
  echo "Run the setup script again or install gh, then authenticate with gh auth login."
  exit 2
fi

if ! gh auth status --hostname github.com >/dev/null 2>&1; then
  echo "GitHub authentication is not configured in this Ubuntu proot."
  echo "Run: gh auth login --hostname github.com --git-protocol https --web"
  exit 2
fi

gh auth setup-git

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

# Build the public handoff from a fresh copy of main. The private local DB/raw
# archive never enters this repository clone.
git clone --depth 1 --branch main "$REPO_URL" "$workdir/repo"
cd "$workdir/repo"
git checkout -B "$PUBLISH_BRANCH"

rm -rf site
mkdir -p site
cp -a "$SITE_DIR"/. site/

# Final check on the exact bytes that are about to be staged.
test ! -e site/data/raw
test ! -e site/tracker.duckdb
test ! -e site/api/review-queue.json
if find site -type f \( -name '*.duckdb' -o -name '*.duckdb.wal' \) -print -quit | grep -q .; then
  echo "Refusing to publish: database file reached handoff clone."
  exit 1
fi

git add -f site

unexpected="$(git diff --cached --name-only | grep -v '^site/' || true)"
if [[ -n "$unexpected" ]]; then
  echo "Refusing to publish unexpected staged paths:"
  echo "$unexpected"
  exit 1
fi

if git diff --cached --quiet; then
  echo "Sanitized site is unchanged; nothing to publish."
  exit 0
fi

git config user.name "Cumbria Records Android Collector"
git config user.email "actions@users.noreply.github.com"
git commit -m "Publish sanitized Android collection"

# Fetch the current machine-generated branch so force-with-lease protects us
# from overwriting an unexpected concurrent update.
git fetch origin "$PUBLISH_BRANCH:refs/remotes/origin/$PUBLISH_BRANCH" 2>/dev/null || true
if git show-ref --verify --quiet "refs/remotes/origin/$PUBLISH_BRANCH"; then
  git push --force-with-lease="refs/heads/$PUBLISH_BRANCH:$(git rev-parse refs/remotes/origin/$PUBLISH_BRANCH)" \
    origin "HEAD:refs/heads/$PUBLISH_BRANCH"
else
  git push origin "HEAD:refs/heads/$PUBLISH_BRANCH"
fi

echo "Sanitized site pushed to $PUBLISH_BRANCH. GitHub-hosted Pages deployment will take over."
