from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher

import duckdb


SCHEMA = """
CREATE TABLE IF NOT EXISTS entity_aliases (
    alias_id VARCHAR PRIMARY KEY,
    entity_id VARCHAR NOT NULL,
    alias_text VARCHAR NOT NULL,
    normalized_alias VARCHAR NOT NULL,
    alias_type VARCHAR NOT NULL DEFAULT 'canonical',
    source VARCHAR,
    confidence DOUBLE NOT NULL DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS entity_aliases_norm_idx ON entity_aliases(normalized_alias);

CREATE TABLE IF NOT EXISTS entity_identifiers (
    entity_id VARCHAR NOT NULL,
    scheme VARCHAR NOT NULL,
    identifier VARCHAR NOT NULL,
    source VARCHAR,
    confidence DOUBLE NOT NULL DEFAULT 1.0,
    PRIMARY KEY(entity_id, scheme, identifier)
);
CREATE INDEX IF NOT EXISTS entity_identifiers_lookup_idx
    ON entity_identifiers(scheme, identifier);

CREATE TABLE IF NOT EXISTS entity_matches (
    match_id VARCHAR PRIMARY KEY,
    left_entity_id VARCHAR NOT NULL,
    right_entity_id VARCHAR NOT NULL,
    confidence DOUBLE NOT NULL,
    match_method VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    evidence_json VARCHAR NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT current_timestamp,
    updated_at TIMESTAMP DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS entity_matches_status_idx ON entity_matches(status);

CREATE TABLE IF NOT EXISTS resolved_entity_members (
    entity_id VARCHAR PRIMARY KEY,
    canonical_entity_id VARCHAR NOT NULL,
    decision_source VARCHAR NOT NULL,
    updated_at TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS review_queue (
    review_id VARCHAR PRIMARY KEY,
    item_type VARCHAR NOT NULL,
    score DOUBLE NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'open',
    summary VARCHAR NOT NULL,
    subject_entity_id VARCHAR,
    object_entity_id VARCHAR,
    evidence_json VARCHAR NOT NULL DEFAULT '[]',
    metadata_json VARCHAR NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT current_timestamp,
    updated_at TIMESTAMP DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS review_queue_status_idx ON review_queue(status);
"""


ORG_TYPES = {
    "ORGANISATION",
    "COMPANY",
    "CHARITY",
    "SUPPLIER",
    "OUTSIDE_BODY",
    "COUNCIL",
}

_SUFFIXES = (
    "limited liability partnership",
    "charitable incorporated organisation",
    "community interest company",
    "public limited company",
    "company limited by guarantee",
    "limited",
    "ltd",
    "plc",
    "llp",
    "cic",
    "cio",
)


def ensure_resolution_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA)


def normalize_org_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.replace("&", " and ")
    value = re.sub(r"[’'`]", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    if value.startswith("the "):
        value = value[4:]
    changed = True
    while changed and value:
        changed = False
        for suffix in _SUFFIXES:
            if value != suffix and value.endswith(" " + suffix):
                value = value[: -(len(suffix) + 1)].strip()
                changed = True
                break
    return value


def _pair_id(left: str, right: str, method: str) -> str:
    a, b = sorted((left, right))
    return hashlib.sha256(f"{a}\0{b}\0{method}".encode()).hexdigest()


def _review_id(
    item_type: str,
    subject: str | None,
    object_: str | None,
    evidence: list[str],
) -> str:
    key = "\0".join([item_type, subject or "", object_ or "", *sorted(evidence)])
    return hashlib.sha256(key.encode()).hexdigest()


def add_alias(
    con: duckdb.DuckDBPyConnection,
    *,
    entity_id: str,
    alias_text: str,
    alias_type: str = "canonical",
    source: str | None = None,
    confidence: float = 1.0,
) -> str:
    normalized = normalize_org_name(alias_text)
    alias_id = hashlib.sha256(
        f"{entity_id}\0{alias_type}\0{normalized}\0{source or ''}".encode()
    ).hexdigest()
    con.execute(
        """INSERT INTO entity_aliases(
             alias_id,entity_id,alias_text,normalized_alias,alias_type,source,confidence
           ) VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(alias_id) DO UPDATE SET
             alias_text=excluded.alias_text,
             normalized_alias=excluded.normalized_alias,
             confidence=excluded.confidence""",
        [alias_id, entity_id, alias_text.strip(), normalized, alias_type, source, confidence],
    )
    return alias_id


def add_identifier(
    con: duckdb.DuckDBPyConnection,
    *,
    entity_id: str,
    scheme: str,
    identifier: str,
    source: str | None = None,
    confidence: float = 1.0,
) -> None:
    scheme = scheme.strip().casefold()
    identifier = re.sub(r"\s+", "", str(identifier)).upper()
    if not scheme or not identifier:
        return
    con.execute(
        """INSERT INTO entity_identifiers(entity_id,scheme,identifier,source,confidence)
           VALUES (?,?,?,?,?)
           ON CONFLICT(entity_id,scheme,identifier) DO UPDATE SET
             source=coalesce(excluded.source,entity_identifiers.source),
             confidence=greatest(entity_identifiers.confidence,excluded.confidence)""",
        [entity_id, scheme, identifier, source, confidence],
    )


def refresh_canonical_aliases(con: duckdb.DuckDBPyConnection) -> int:
    rows = con.execute(
        "SELECT entity_id, canonical_name, entity_type, metadata_json FROM entities"
    ).fetchall()
    count = 0
    for entity_id, name, entity_type, metadata_json in rows:
        if entity_type not in ORG_TYPES:
            continue
        source = None
        identifiers: list[dict] = []
        try:
            metadata = json.loads(metadata_json or "{}")
            source = metadata.get("source_system")
            raw_identifiers = metadata.get("identifiers") or []
            if isinstance(raw_identifiers, list):
                identifiers = [item for item in raw_identifiers if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass
        add_alias(
            con,
            entity_id=entity_id,
            alias_text=name,
            alias_type="canonical",
            source=source,
        )
        for item in identifiers:
            scheme = item.get("scheme")
            value = item.get("id") or item.get("identifier")
            if scheme and value:
                add_identifier(
                    con,
                    entity_id=entity_id,
                    scheme=str(scheme),
                    identifier=str(value),
                    source=source,
                )
        count += 1
    return count


def _upsert_match(
    con: duckdb.DuckDBPyConnection,
    *,
    left: str,
    right: str,
    confidence: float,
    method: str,
    status: str,
    evidence: dict,
) -> str:
    if left == right:
        return ""
    a, b = sorted((left, right))
    match_id = _pair_id(a, b, method)
    con.execute(
        """INSERT INTO entity_matches(
             match_id,left_entity_id,right_entity_id,confidence,match_method,status,evidence_json
           ) VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(match_id) DO UPDATE SET
             confidence=excluded.confidence,
             status=CASE
               WHEN entity_matches.status IN ('accepted','rejected') THEN entity_matches.status
               ELSE excluded.status
             END,
             evidence_json=excluded.evidence_json,
             updated_at=now()""",
        [
            match_id,
            a,
            b,
            confidence,
            method,
            status,
            json.dumps(evidence, sort_keys=True, ensure_ascii=False),
        ],
    )
    return match_id


def put_review(
    con: duckdb.DuckDBPyConnection,
    *,
    item_type: str,
    score: float,
    summary: str,
    subject: str | None,
    object_: str | None,
    evidence: list[str],
    metadata: dict | None = None,
) -> str:
    review_id = _review_id(item_type, subject, object_, evidence)
    con.execute(
        """INSERT INTO review_queue(
             review_id,item_type,score,summary,subject_entity_id,object_entity_id,
             evidence_json,metadata_json
           ) VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(review_id) DO UPDATE SET
             score=excluded.score,
             summary=excluded.summary,
             metadata_json=excluded.metadata_json,
             updated_at=now()""",
        [
            review_id,
            item_type,
            score,
            summary,
            subject,
            object_,
            json.dumps(sorted(evidence), ensure_ascii=False),
            json.dumps(metadata or {}, sort_keys=True, ensure_ascii=False),
        ],
    )
    return review_id


def propose_entity_matches(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    ensure_resolution_schema(con)
    refresh_canonical_aliases(con)
    stats = {"identifier": 0, "name_exact": 0, "fuzzy_review": 0}

    identifier_groups = con.execute(
        """SELECT scheme, identifier, list(entity_id)
           FROM entity_identifiers
           GROUP BY scheme, identifier
           HAVING count(DISTINCT entity_id) > 1"""
    ).fetchall()
    for scheme, identifier, entity_ids in identifier_groups:
        unique = sorted(set(entity_ids))
        for index, left in enumerate(unique):
            for right in unique[index + 1 :]:
                _upsert_match(
                    con,
                    left=left,
                    right=right,
                    confidence=1.0,
                    method="identifier_exact",
                    status="auto_accepted",
                    evidence={"scheme": scheme, "identifier": identifier},
                )
                stats["identifier"] += 1

    exact_groups = con.execute(
        """SELECT normalized_alias, list(DISTINCT entity_id)
           FROM entity_aliases
           WHERE length(normalized_alias) >= 5
           GROUP BY normalized_alias
           HAVING count(DISTINCT entity_id) > 1"""
    ).fetchall()
    for normalized, entity_ids in exact_groups:
        unique = sorted(set(entity_ids))
        token_count = len(normalized.split())
        confidence = 0.97 if token_count >= 2 or len(normalized) >= 10 else 0.93
        # A normalized name alone is not a unique identifier. Distinct companies,
        # suppliers and public bodies can legitimately share a name, especially
        # after legal suffixes are removed. Keep name-only links in human review;
        # exact identifiers remain the only automatic merge path.
        for index, left in enumerate(unique):
            for right in unique[index + 1 :]:
                match_id = _upsert_match(
                    con,
                    left=left,
                    right=right,
                    confidence=confidence,
                    method="normalized_name_exact",
                    status="review",
                    evidence={"normalized_name": normalized, "name_only": True},
                )
                stats["name_exact"] += 1
                put_review(
                    con,
                    item_type="ENTITY_MATCH",
                    score=confidence,
                    summary=f"Possible entity match on normalized name: {normalized}",
                    subject=left,
                    object_=right,
                    evidence=[match_id],
                    metadata={"method": "normalized_name_exact", "name_only": True},
                )

    aliases = con.execute(
        """SELECT entity_id, normalized_alias
           FROM entity_aliases
           WHERE length(normalized_alias) >= 8"""
    ).fetchall()
    buckets: dict[str, list[tuple[str, str]]] = {}
    for entity_id, normalized in aliases:
        parts = normalized.split()
        if parts:
            buckets.setdefault(parts[0], []).append((entity_id, normalized))
    seen_pairs: set[tuple[str, str]] = set()
    for bucket in buckets.values():
        if len(bucket) > 80:
            continue
        for index, (left_id, left_name) in enumerate(bucket):
            for right_id, right_name in bucket[index + 1 :]:
                if left_id == right_id:
                    continue
                pair = tuple(sorted((left_id, right_id)))
                if pair in seen_pairs or left_name == right_name:
                    continue
                seen_pairs.add(pair)
                ratio = SequenceMatcher(None, left_name, right_name).ratio()
                if ratio < 0.88:
                    continue
                match_id = _upsert_match(
                    con,
                    left=left_id,
                    right=right_id,
                    confidence=round(ratio, 4),
                    method="name_similarity",
                    status="review",
                    evidence={"left": left_name, "right": right_name},
                )
                put_review(
                    con,
                    item_type="ENTITY_MATCH",
                    score=ratio,
                    summary=f"Similar organisation names: {left_name} / {right_name}",
                    subject=left_id,
                    object_=right_id,
                    evidence=[match_id],
                    metadata={"method": "name_similarity"},
                )
                stats["fuzzy_review"] += 1

    rebuild_resolved_members(con)
    return stats


class _DSU:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, value: str) -> str:
        self.parent.setdefault(value, value)
        if self.parent[value] != value:
            self.parent[value] = self.find(self.parent[value])
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        canonical = min(left_root, right_root)
        other = right_root if canonical == left_root else left_root
        self.parent[other] = canonical


def rebuild_resolved_members(con: duckdb.DuckDBPyConnection) -> int:
    ensure_resolution_schema(con)
    dsu = _DSU()
    entities = [row[0] for row in con.execute("SELECT entity_id FROM entities").fetchall()]
    for entity_id in entities:
        dsu.find(entity_id)
    rows = con.execute(
        """SELECT left_entity_id,right_entity_id
           FROM entity_matches
           WHERE status IN ('auto_accepted','accepted')"""
    ).fetchall()
    for left, right in rows:
        dsu.union(left, right)

    con.execute("DELETE FROM resolved_entity_members")
    for entity_id in entities:
        con.execute(
            """INSERT INTO resolved_entity_members(
                 entity_id,canonical_entity_id,decision_source
               ) VALUES (?,?,?)""",
            [entity_id, dsu.find(entity_id), "accepted_entity_matches"],
        )
    return len(entities)


def queue_declared_interest_candidates(con: duckdb.DuckDBPyConnection) -> int:
    ensure_resolution_schema(con)
    aliases = con.execute(
        """SELECT DISTINCT a.entity_id,a.alias_text,a.normalized_alias
           FROM entity_aliases a
           JOIN latest_facts sf ON sf.subject_entity_id=a.entity_id
           WHERE sf.predicate='SUPPLIER_TO_CONTRACT'
             AND length(a.normalized_alias) >= 6"""
    ).fetchall()
    if not aliases:
        return 0
    register_rows = con.execute(
        """SELECT fact_id,subject_entity_id,value_text
           FROM latest_facts
           WHERE fact_type='REGISTER_OF_INTEREST_ENTRY'
             AND value_text IS NOT NULL
             AND value_text <> '[REDACTED_FROM_STRUCTURED_DATA]'"""
    ).fetchall()
    count = 0
    for fact_id, person_id, text in register_rows:
        normalized_text = normalize_org_name(text)
        padded = f" {normalized_text} "
        for entity_id, alias_text, normalized_alias in aliases:
            if len(normalized_alias.split()) == 1 and len(normalized_alias) < 9:
                continue
            if f" {normalized_alias} " not in padded:
                continue
            put_review(
                con,
                item_type="DECLARED_INTEREST_ENTITY_CANDIDATE",
                score=0.82,
                summary=f"Declared-interest text may reference contract supplier {alias_text}",
                subject=person_id,
                object_=entity_id,
                evidence=[fact_id],
                metadata={
                    "match_method": "normalized_exact_phrase",
                    "matched_alias": alias_text,
                },
            )
            count += 1
    return count


def run_resolution(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    stats = propose_entity_matches(con)
    stats["interest_candidates"] = queue_declared_interest_candidates(con)
    return stats
