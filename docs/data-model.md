# Evidence and data model

The tracker separates **documents**, **snapshots**, **observations**, **claims**, **entities** and **relationships** on purpose.

- A **document** is the logical public record at a canonical URL.
- A **snapshot** is one archived byte-for-byte version of that document, identified by SHA-256.
- An **observation** records that a specific snapshot was retrieved at a particular time. Re-fetching unchanged content adds an observation without storing another copy of the source bytes.
- A **claim** is a proposition someone may want to assess. It is never created as established fact merely because an anomaly exists.
- **claim_evidence** links a claim to a specific document snapshot and locator (page, paragraph, table row, etc.).
- An **entity** is a person, organisation, committee, supplier, project or other named thing.
- A **relationship** is only stored with an evidence-bearing document and evidence class.
- An **event** is a timeline item linked back to its evidence.

## Evidence hierarchy

`OFFICIAL_RECORD`, `REGULATORY_FINDING` and `COURT_RECORD` identify the nature of the source, not whether every statement inside it is necessarily true. `MEDIA_REPORT` and `PUBLIC_ALLEGATION` require attribution. `INFERENCE` must remain visibly inferential. `UNVERIFIED_LEAD` must never be rendered as a finding.

## Archive layout

Raw bytes are content-addressed under `data/raw/blobs/sha256/`. Snapshot metadata is stored separately by source and snapshot ID, while retrieval observations are small timestamped JSON records. This prevents unchanged PDFs or HTML being committed repeatedly while still preserving when each source was checked.

## Change detection

A canonical URL is stable across query-order differences and fragments. When the bytes at the same canonical URL change, a new snapshot is preserved and a `CONTENT_CHANGED` event records the old and new hashes. Previous snapshots are not overwritten.

## Future entity resolution

Automated matching should be conservative. Exact identifiers (Companies House number, supplier ID, committee ID) should outrank fuzzy names. Fuzzy matches should carry a confidence score and require review before being promoted into durable relationships.
