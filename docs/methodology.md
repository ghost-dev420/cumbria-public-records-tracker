# Methodology

This project is an evidence index and research aid. It does not determine that corruption, misconduct, conflicts of interest, criminality or improper motive occurred.

Every structured fact is tied to a source document and an exact archived snapshot. The audit chain is:

`fact -> source document -> locator -> snapshot -> SHA-256 -> retrieval observation`

Source classes remain distinct: official records, regulatory findings, court records, media reporting, attributed public allegations, inference and unverified leads.

Entity resolution is non-destructive. Exact stable public identifiers may be accepted automatically; high-confidence normalized-name matches may be accepted only when sufficiently specific; fuzzy matches require human review. Original source entities are never deleted by a match decision.

Automated pattern outputs are review signals, not findings. The public accountability dashboard distinguishes four materially different things:

1. **Documented links** — independent public datasets resolve to the same supplier or entity.
2. **Coverage gaps** — the tracker has not yet located a matching procurement record or complete contract period. A coverage gap is not evidence that no lawful contract, framework, call-off, variation or other authority exists.
3. **Reviewed connections / concerns** — potentially reputational relationship or value-for-money signals that have passed explicit publication review.
4. **Documented findings** — approved claims supported by an authoritative source. The site does not upgrade an analytical pattern into a documented finding by itself.

A narrow allow-list of neutral coverage observations may be published automatically in the temporary sanitized publication database. The working database remains unchanged. Signals that could imply waste, conflict of interest, family/friend connections, misconduct or improper motive are never auto-published.

Companies House and other registry relationships are used to create private research candidates. Exact-name matches between a person with significant control and a public office-holder remain review candidates because a name match alone does not establish identity. A declaration-of-interest entry that names a Companies House PSC is also reviewed before publication. Relationship words such as spouse, partner or relative are retained as review context only; the system does not infer a family relationship merely from shared names, addresses or company links.

Public presentation shows the supporting fact chain rather than a rating of any person or public body. Amounts attached to coverage gaps are the payment streams represented by the observation; they are not automatically amounts of waste or loss.

The structured index excludes unnecessary private contact details and sensitive personal data. Collection uses a transparent user agent, rate limiting and robots.txt checks. Access-control responses are recorded as blocked rather than bypassed.

The public repository excludes the unreviewed raw archive by default. Publishing archived documents or generated evidence packages is a separate review step.
