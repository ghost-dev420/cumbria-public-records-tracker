# Cumbria Public Records Evidence Tracker

An evidence-first project for collecting, preserving, cross-referencing and presenting public records relating to councils, public bodies and associated organisations across Cumbria.

The project is designed to keep a clear distinction between primary records, official findings, reporting, allegations, inference and unverified leads. A relationship, anomaly or review signal is not treated as evidence of wrongdoing by itself.

## Public accountability dashboard

The generated site includes a plain-language accountability dashboard alongside the raw evidence index. It separates:

- documented payment / contract links
- contract and procurement coverage gaps
- reviewed value-for-money concerns
- reviewed declared-interest / supplier connections
- documented findings that have passed the publication boundary

Neutral coverage observations can be published automatically from the sanitized publication copy. Signals that could imply waste, conflict, family/friend connections, misconduct or improper motive remain private until explicitly reviewed and approved.

## Public-release principles

- every published fact should retain a source URL and evidence locator
- archived versions are identified by SHA-256 and retrieval time
- entity matching is non-destructive and auditable
- fuzzy identity matches require human review
- automated patterns are review signals, not findings or ratings of people
- unnecessary private or sensitive personal data is excluded from the structured index
- access controls and robots.txt are respected rather than bypassed

## Coverage

The project is intended to cover Cumberland Council, Westmorland and Furness Council, town and parish councils across Cumbria, predecessor authorities, procurement and transparency records, regulatory decisions, Companies House and Charity Commission reference data, and other relevant official public sources.

Companies House PSC data is used to create private candidates where a supplier's person with significant control also appears in public office-holder or declaration-of-interest records. Name matches alone are never treated as proof of identity or a conflict.

## Automated production run (no Android required)

**The public dashboard is now refreshed by GitHub-hosted runners** using `.github/workflows/hosted-production.yml` (daily at 04:19 UTC; can also be started manually). It installs Playwright/Chromium, collects the full configured sources, checks that real source facts were obtained, builds the publication-gated `site/`, verifies that review material and databases are excluded, and deploys **only** that site directly to GitHub Pages. No phone, self-hosted runner, publishing PAT, or personal computer is needed.

Important distinction: each public GitHub-hosted run uses an **ephemeral database**. It is a current public snapshot, not a replacement for the accumulated private evidence archive or the phone's historical DuckDB. The private `Cumberland-Council-Corruption-Tracker` repository continues its independent daily GitHub-hosted evidence collection and stores its raw archival snapshots privately. Cloud-hosted collection may report blocked/unavailable sources; a failed or near-empty run **does not overwrite** the existing public site.

## Optional Android production (when the device is repaired)

The optional persistent Android production database lives on the Android/Linux collector rather than in GitHub Actions. The one-command production pipeline is:

```bash
bash scripts/update-production.sh
```

If the phone comes back online, this optional pipeline backs up its DuckDB, refreshes public sources, periodically refreshes registry/PSC data, runs analysis, and builds a sanitized site. Its `android-publish` branch is retained for historical auditing only; the cloud-hosted workflow is now the sole GitHub Pages deployer.

Android/Termux runs are scheduled locally with `bash scripts/install-termux-production-cron.sh` from native Termux. The job launches `scripts/update-production.sh` inside Ubuntu against the persistent database with backup and locking protections. The heavier Companies House/Charity reference refresh defaults to every 14 days and PSC data every 30 days. `.github/workflows/production-android.yml` is now a **manual-only** fallback for an online Linux self-hosted runner labelled `cumbria-browser`, not an Android scheduler.

The ordinary GitHub-hosted `collect-and-publish.yml` remains a fresh-database smoke test and is deliberately not the production state store.

## Documentation

- [Methodology](docs/methodology.md)
- [Official registry reference data](docs/reference-data.md)

## Public/private boundary

The private development repository remains the collection and QA workspace. This public repository intentionally does **not** contain the unreviewed `data/raw/` archive. Publication of source documents, generated API data and evidence packages is a separate reviewed step.

## Suggest a source

Use the repository issue form to suggest a verifiable public record, dataset or official source. Please do not submit private addresses, private contact details, medical information or other sensitive personal data.
