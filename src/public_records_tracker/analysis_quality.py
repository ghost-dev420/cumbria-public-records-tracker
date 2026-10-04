from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict

import duckdb

from .resolution import ORG_TYPES, ensure_resolution_schema, normalize_org_name
from .supplier_hygiene import split_payment_channel


_PROCUREMENT_SOURCE_TERMS = (
    "contracts finder",
    "find a tender",
    "ocds",
    "procurement",
)
_TYPE_RANK = {
    "COUNCIL": 5,
    "COMPANY": 4,
    "CHARITY": 4,
    "ORGANISATION": 3,
    "SUPPLIER": 2,
    "OUTSIDE_BODY": 1,
}
_LEGAL_SUFFIXES = (
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


def _pair_id(left: str, right: str, method: str) -> str:
    a, b = sorted((left, right))
    return hashlib.sha256(f"{a}\0{b}\0{method}".encode()).hexdigest()


def analysis_org_key(value: str) -> str:
    cleaned, _ = split_payment_channel(value)
    return normalize_org_name(cleaned)


def _upsert_auto_match(
    con: duckdb.DuckDBPyConnection,
    *,
    left: str,
    right: str,
    method: str,
    confidence: float,
    evidence: dict,
) -> str:
    a, b = sorted((left, right))
    match_id = _pair_id(a, b, method)
    con.execute(
        """INSERT INTO entity_matches(
             match_id,left_entity_id,right_entity_id,confidence,
             match_method,status,evidence_json
           ) VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(match_id) DO UPDATE SET
             confidence=excluded.confidence,
             status=CASE
               WHEN entity_matches.status IN ('accepted','rejected')
                 THEN entity_matches.status
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
            "auto_accepted",
            json.dumps(evidence, sort_keys=True, ensure_ascii=False),
        ],
    )
    return match_id


def ensure_payment_channel_matches(con: duckdb.DuckDBPyConnection) -> int:
    """Auto-link entities differing only by a known payment-channel suffix."""
    ensure_resolution_schema(con)
    rows = con.execute(
        "SELECT entity_id,canonical_name,entity_type FROM entities"
    ).fetchall()
    groups: dict[str, list[tuple[str, str, str | None]]] = defaultdict(list)
    for entity_id, name, entity_type in rows:
        if str(entity_type) not in ORG_TYPES:
            continue
        cleaned, channel = split_payment_channel(str(name))
        key = normalize_org_name(cleaned)
        if key:
            groups[key].append((str(entity_id), str(name), channel))

    inserted = 0
    for key, members in groups.items():
        if len(members) < 2 or not any(channel for _, _, channel in members):
            continue
        for index, (left_id, left_name, left_channel) in enumerate(members):
            for right_id, right_name, right_channel in members[index + 1 :]:
                if not (left_channel or right_channel):
                    continue
                _upsert_auto_match(
                    con,
                    left=left_id,
                    right=right_id,
                    method="payment_channel_normalized",
                    confidence=0.99,
                    evidence={
                        "normalized_name": key,
                        "left_name": left_name,
                        "right_name": right_name,
                        "left_channel": left_channel,
                        "right_channel": right_channel,
                    },
                )
                inserted += 1
    return inserted


def _raw_legal_suffix(value: str) -> str | None:
    cleaned = value.casefold().replace("&", " and ")
    cleaned = re.sub(r"[’'`]", "", cleaned)
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for suffix in _LEGAL_SUFFIXES:
        if cleaned != suffix and cleaned.endswith(" " + suffix):
            return suffix
    return None


def ensure_legal_suffix_matches(con: duckdb.DuckDBPyConnection) -> int:
    """Auto-link exact organisation names that differ only by a legal suffix.

    This is narrower than the general normalized-name resolver: one side must
    actually contain a recognised UK-style legal suffix such as Ltd/Limited or
    plc, and the suffix-stripped organisation key must be identical.
    """
    ensure_resolution_schema(con)
    groups: dict[str, list[tuple[str, str, str | None]]] = defaultdict(list)
    for entity_id, name, entity_type in con.execute(
        "SELECT entity_id,canonical_name,entity_type FROM entities"
    ).fetchall():
        if str(entity_type) not in ORG_TYPES:
            continue
        key = normalize_org_name(str(name))
        if key:
            groups[key].append(
                (str(entity_id), str(name), _raw_legal_suffix(str(name)))
            )

    inserted = 0
    for key, members in groups.items():
        if len(members) < 2 or not any(suffix for _, _, suffix in members):
            continue
        for index, (left_id, left_name, left_suffix) in enumerate(members):
            for right_id, right_name, right_suffix in members[index + 1 :]:
                if not (left_suffix or right_suffix):
                    continue
                _upsert_auto_match(
                    con,
                    left=left_id,
                    right=right_id,
                    method="legal_suffix_normalized",
                    confidence=0.99,
                    evidence={
                        "normalized_name": key,
                        "left_name": left_name,
                        "right_name": right_name,
                        "left_legal_suffix": left_suffix,
                        "right_legal_suffix": right_suffix,
                    },
                )
                inserted += 1
    return inserted


def _edit_distance_at_most_one(left: str, right: str) -> bool:
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right, strict=True)) <= 1
    if len(left) > len(right):
        left, right = right, left
    i = j = differences = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
            continue
        differences += 1
        if differences > 1:
            return False
        j += 1
    return True


def ensure_near_exact_typo_matches(con: duckdb.DuckDBPyConnection) -> int:
    """Auto-link only long, multi-token organisation names one edit apart.

    This catches obvious transcription errors such as "Infrastructire" versus
    "Infrastructure" without turning general fuzzy matching into an automatic
    merge rule. Short/single-token names remain human-review only.
    """
    ensure_resolution_schema(con)
    buckets: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for entity_id, name, entity_type in con.execute(
        "SELECT entity_id,canonical_name,entity_type FROM entities"
    ).fetchall():
        if str(entity_type) not in ORG_TYPES:
            continue
        key = normalize_org_name(str(name))
        parts = key.split()
        if len(key) < 20 or len(parts) < 2:
            continue
        buckets[parts[0]].append((str(entity_id), str(name), key))

    inserted = 0
    seen: set[tuple[str, str]] = set()
    for members in buckets.values():
        if len(members) > 80:
            continue
        for index, (left_id, left_name, left_key) in enumerate(members):
            for right_id, right_name, right_key in members[index + 1 :]:
                pair = tuple(sorted((left_id, right_id)))
                if pair in seen or left_key == right_key:
                    continue
                seen.add(pair)
                if len(left_key.split()) != len(right_key.split()):
                    continue
                if not _edit_distance_at_most_one(left_key, right_key):
                    continue
                _upsert_auto_match(
                    con,
                    left=left_id,
                    right=right_id,
                    method="near_exact_typo",
                    confidence=0.995,
                    evidence={
                        "left_name": left_name,
                        "right_name": right_name,
                        "left_normalized": left_key,
                        "right_normalized": right_key,
                        "maximum_edit_distance": 1,
                    },
                )
                inserted += 1
    return inserted


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
        if left_root != right_root:
            self.parent[right_root] = left_root


def _metadata(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _representative_score(
    *,
    name: str,
    entity_type: str,
    metadata: dict,
    identifier_count: int,
    spelling_consensus: int,
) -> tuple[int, int, int, int, int, int, int]:
    _, channel = split_payment_channel(name)
    source = str(metadata.get("source_system") or "").casefold()
    procurement_source = int(any(term in source for term in _PROCUREMENT_SOURCE_TERMS))
    council = int(entity_type == "COUNCIL")
    clean_name = int(channel is None)
    normalized_length = min(len(normalize_org_name(name)), 120)
    return (
        council,
        spelling_consensus,
        int(identifier_count > 0),
        procurement_source,
        clean_name,
        _TYPE_RANK.get(entity_type, 0),
        normalized_length,
    )


def rebuild_quality_resolved_members(con: duckdb.DuckDBPyConnection) -> int:
    """Rebuild resolved membership using evidence quality for the representative."""
    ensure_resolution_schema(con)
    entity_rows = con.execute(
        "SELECT entity_id,canonical_name,entity_type,metadata_json FROM entities"
    ).fetchall()
    dsu = _DSU()
    details: dict[str, tuple[str, str, dict]] = {}
    for entity_id, name, entity_type, metadata_json in entity_rows:
        entity_id = str(entity_id)
        dsu.find(entity_id)
        details[entity_id] = (
            str(name),
            str(entity_type),
            _metadata(metadata_json),
        )

    for left, right in con.execute(
        """SELECT left_entity_id,right_entity_id
           FROM entity_matches
           WHERE status IN ('auto_accepted','accepted')"""
    ).fetchall():
        dsu.union(str(left), str(right))

    identifier_counts = {
        str(entity_id): int(count)
        for entity_id, count in con.execute(
            """SELECT entity_id,count(*)
               FROM entity_identifiers
               GROUP BY entity_id"""
        ).fetchall()
    }
    components: dict[str, list[str]] = defaultdict(list)
    for entity_id in details:
        components[dsu.find(entity_id)].append(entity_id)

    representatives: dict[str, str] = {}
    for root, members in components.items():
        spelling_counts = Counter(normalize_org_name(details[item][0]) for item in members)
        scores: dict[str, tuple[int, int, int, int, int, int, int]] = {}
        for entity_id in members:
            name, entity_type, metadata = details[entity_id]
            scores[entity_id] = _representative_score(
                name=name,
                entity_type=entity_type,
                metadata=metadata,
                identifier_count=identifier_counts.get(entity_id, 0),
                spelling_consensus=spelling_counts[normalize_org_name(name)],
            )
        best = max(scores.values())
        representatives[root] = min(
            entity_id for entity_id, score in scores.items() if score == best
        )

    con.execute("DELETE FROM resolved_entity_members")
    for entity_id in details:
        root = dsu.find(entity_id)
        representative = representatives[root]
        decision = (
            "accepted_entity_matches:quality_preferred"
            if len(components[root]) > 1
            else "identity"
        )
        con.execute(
            """INSERT INTO resolved_entity_members(
                 entity_id,canonical_entity_id,decision_source
               ) VALUES (?,?,?)""",
            [entity_id, representative, decision],
        )
    return len(details)


def close_resolved_entity_match_reviews(con: duckdb.DuckDBPyConnection) -> int:
    """Close fuzzy/manual reviews whose entities are already safely resolved."""
    rows = con.execute(
        """SELECT r.review_id
           FROM review_queue r
           JOIN resolved_entity_members left_member
             ON left_member.entity_id=r.subject_entity_id
           JOIN resolved_entity_members right_member
             ON right_member.entity_id=r.object_entity_id
           WHERE r.item_type='ENTITY_MATCH'
             AND r.status='open'
             AND left_member.canonical_entity_id=right_member.canonical_entity_id"""
    ).fetchall()
    if not rows:
        return 0
    con.executemany(
        "UPDATE review_queue SET status='resolved',updated_at=now() WHERE review_id=?",
        rows,
    )
    return len(rows)


def prepare_analysis_resolution(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    channel_matches = ensure_payment_channel_matches(con)
    legal_suffix_matches = ensure_legal_suffix_matches(con)
    typo_matches = ensure_near_exact_typo_matches(con)
    resolved = rebuild_quality_resolved_members(con)
    closed_reviews = close_resolved_entity_match_reviews(con)
    return {
        "payment_channel_matches": channel_matches,
        "legal_suffix_matches": legal_suffix_matches,
        "near_exact_typo_matches": typo_matches,
        "quality_resolved_entities": resolved,
        "resolved_entity_match_reviews": closed_reviews,
    }
