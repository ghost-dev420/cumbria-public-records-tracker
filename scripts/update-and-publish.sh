#!/usr/bin/env bash
set -Eeuo pipefail

REPO_DIR="${REPO_DIR:-/root/cumbria-public-records-tracker}"
exec bash "$REPO_DIR/scripts/update-production.sh"
