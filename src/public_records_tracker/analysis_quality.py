from __future__ import annotations

import hashlib
import json
from collections import defaultdict

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


def _pair_id(left: str, right: str, method: str) -> str:
    a, b = sorted((left, right))
    return hashlib.sha256(f"{a}\0{b}\0{method}".encode()).hexdigest()


def analysis_org_key(value: str) -> str:
    cleaned, _ = split_payment_channel(value)
    return normalize_org_name(cleaned)


def ensure_payment_channel_matches(con: duckdb.DuckDBPyConnection) -> int:
    """Auto-link entities differing only by a known payment-channel suffix.

    The rule is deliberately narrow: at least one side must actually carry a
    recognised channel suffix, and the remaining normalised organisation name
    must be identical.
    """
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
                a, b = sorted((left_id, right_id))
                match_id = _pair_id(a, b, "payment_channel_normalized")
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
                        0.99,
                        "payment_channel_normalized",
                        "auto_accepted",
                        json.dumps(
                            {
                                "normalized_name": key,
                                "left_name": left_name,
                                "right_name": right_name,
                                "left_channel": left_channel,
                                "right_channel": right_channel,
                            },
                            sort_keys=True,
                            ensure_ascii=False,
                        ),
                    ],
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
) -> tuple[int, int, int, int, int, int]:
    _, channel = split_payment_channel(name)
    source = str(metadata.get("source_system") or "").casefold()
    procurement_source = int(any(term in source for term in _PROCUREMENT_SOURCE_TERMS))
    council = int(entity_type == "COUNCIL")
    clean_name = int(channel is None)
    normalized_length = min(len(normalize_org_name(name)), 120)
    return (
        int(identifier_count > 0),
        council,
        procurement_source,
        clean_name,
        _TYPE_RANK.get(entity_type, 0),
        normalized_length,
    )


def rebuild_quality_resolved_members(con: duckdb.DuckDBPyConnection) -> int:
    """Rebuild resolved membership using evidence quality for the representative.

    Accepted matches still define membership.  The only change is which member
    is used as the component's canonical representative: identifier-backed and
    authoritative/procurement entities beat payment aliases and dirty names.
    """
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
        scores: dict[str, tuple[int, int, int, int, int, int]] = {}
        for entity_id in members:
            name, entity_type, metadata = details[entity_id]
            scores[entity_id] = _representative_score(
                name=name,
                entity_type=entity_type,
                metadata=metadata,
                identifier_count=identifier_counts.get(entity_id, 0),
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


def prepare_analysis_resolution(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    channel_matches = ensure_payment_channel_matches(con)
    resolved = rebuild_quality_resolved_members(con)
    return {
        "payment_channel_matches": channel_matches,
        "quality_resolved_entities": resolved,
    }
