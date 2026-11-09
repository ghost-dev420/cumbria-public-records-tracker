#!/usr/bin/env bash
set -Eeuo pipefail

# GitHub-hosted public refresh: bounded representative coverage. The full
# historical archive remains the private repository's responsibility.
# Running all sources including the very large Contracts Finder scan took
# longer than the existing cloud job's available window.
export PYTHONUNBUFFERED=1
CONFIG="${HOSTED_CONFIG:-config/sources-android-full.yml}"
SOURCE_LIMIT_SECONDS="${SOURCE_LIMIT_SECONDS:-240}"
PAGE_LIMIT="${PAGE_LIMIT:-1}"

# Use the sources already observed to produce structured evidence in the
# GitHub-hosted smoke workflow. A single blocked source must not stop others.
# Include two distinct councils and ombudsman/grants coverage.
sources=(
  cumberland_transparency
  westmorland_furness_spending
  lgsco_cumberland
  lgsco_westmorland_furness
  housing_ombudsman_home_group
  westmorland_furness_south_lakeland_grants
  find_a_tender
)

failures=0
for source in "${sources[@]}"; do
  echo "::group::Collect $source (limit: ${SOURCE_LIMIT_SECONDS}s)"
  if timeout --signal=TERM --kill-after=15s "${SOURCE_LIMIT_SECONDS}s" \
      prt collect --source "$source" --page-limit "$PAGE_LIMIT" --config "$CONFIG"; then
    echo "Collector completed: $source"
  else
    status=$?
    echo "::warning::Collector $source exited $status; continuing to other sources."
    failures=$((failures + 1))
  fi
  echo "::endgroup::"
done

echo "Completed bounded cloud collection; failed or interrupted source commands: $failures"
# The workflow's subsequent DB provenance and source-health gates decide
# whether this partial result is sufficient for publication.
