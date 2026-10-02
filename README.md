# Cumbria Public Records Evidence Tracker

An evidence-first project for collecting, preserving, cross-referencing and presenting public records relating to councils, public bodies and associated organisations across Cumbria.

The project is designed to keep a clear distinction between primary records, official findings, reporting, allegations, inference and unverified leads. A relationship, anomaly or review signal is not treated as evidence of wrongdoing by itself.

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

## Documentation

- [Methodology](docs/methodology.md)
- [Official registry reference data](docs/reference-data.md)

## Public/private boundary

The private development repository remains the collection and QA workspace. This public repository intentionally does **not** contain the unreviewed `data/raw/` archive. Publication of source documents, generated API data and evidence packages is a separate reviewed step.

## Suggest a source

Use the repository issue form to suggest a verifiable public record, dataset or official source. Please do not submit private addresses, private contact details, medical information or other sensitive personal data.
