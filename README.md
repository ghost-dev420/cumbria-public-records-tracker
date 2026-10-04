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

## Automated production run

The persistent production database lives on the Android/Linux collector rather than in GitHub Actions. The one-command production pipeline is:

```bash
bash scripts/update-production.sh
```

It backs up the DuckDB, refreshes public sources, periodically refreshes Companies House/Charity reference data and PSC data, runs consolidated analysis, builds the sanitized public site and pushes only `site/` to the `android-publish` branch.

`.github/workflows/production-android.yml` schedules that same pipeline daily on the self-hosted runner labelled `cumbria-browser`. The heavier Companies House/Charity reference refresh defaults to every 14 days and PSC data every 30 days.

The ordinary GitHub-hosted `collect-and-publish.yml` remains a fresh-database smoke test and is deliberately not the production state store.

## Documentation

- [Methodology](docs/methodology.md)
- [Official registry reference data](docs/reference-data.md)

## Public/private boundary

The private development repository remains the collection and QA workspace. This public repository intentionally does **not** contain the unreviewed `data/raw/` archive. Publication of source documents, generated API data and evidence packages is a separate reviewed step.

## Suggest a source

Use the repository issue form to suggest a verifiable public record, dataset or official source. Please do not submit private addresses, private contact details, medical information or other sensitive personal data.
