from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict

import duckdb

from .resolution import ORG_TYPES, ensure_resolution_schema, normalize_org_name
from .supplier_hygiene import is_plausible_org_name


def _pair_id(left: str, right: str, method: str) -> str:
    a, b = sorted((left, right))
    return hashlib.sha256(f"{a}\0{b}\0{method}".encode()).hexdigest()


def _upsert_auto_match(
    con: duckdb.DuckDBPyConnection,
    *,
    left: str,
    right: str,
    method: str,
    confidence: float,
    evidence: dict,
) -> bool:
    if left == right:
        return False
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
    return True


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


def ensure_safe_variant_matches(con: duckdb.DuckDBPyConnection) -> int:
    """Auto-resolve only very conservative organisation-name variants.

    Two rules are allowed:
    * the suffix-normalised names are identical after removing whitespace,
      catching accidental missing/extra spaces (``consultancyand``);
    * long three-token names differ by at most one character, catching obvious
      source typos such as ``Mineral``/``Minerals`` or ``Matters``/``Mattersg``.

    Date-like, numeric and placeholder identities are never eligible.
    """
    ensure_resolution_schema(con)
    rows = []
    for entity_id, name, entity_type in con.execute(
        "SELECT entity_id,canonical_name,entity_type FROM entities"
    ).fetchall():
        if str(entity_type) not in ORG_TYPES or not is_plausible_org_name(str(name)):
            continue
        key = normalize_org_name(str(name))
        if len(key) < 10 or not re.search(r"[a-z]", key):
            continue
        rows.append((str(entity_id), str(name), key))

    inserted = 0
    seen: set[tuple[str, str, str]] = set()

    compact_groups: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for row in rows:
        compact_groups[row[2].replace(" ", "")].append(row)
    for compact, members in compact_groups.items():
        if len(compact) < 15 or len(members) < 2:
            continue
        for index, (left_id, left_name, left_key) in enumerate(members):
            for right_id, right_name, right_key in members[index + 1 :]:
                if left_key == right_key:
                    continue
                pair_key = (*sorted((left_id, right_id)), "whitespace_join_normalized")
                if pair_key in seen:
                    continue
                seen.add(pair_key)
                inserted += int(
                    _upsert_auto_match(
                        con,
                        left=left_id,
                        right=right_id,
                        method="whitespace_join_normalized",
                        confidence=0.997,
                        evidence={
                            "left_name": left_name,
                            "right_name": right_name,
                            "left_normalized": left_key,
                            "right_normalized": right_key,
                            "compact_normalized": compact,
                        },
                    )
                )

    buckets: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for row in rows:
        parts = row[2].split()
        if len(row[2]) >= 15 and len(parts) >= 3:
            buckets[parts[0]].append(row)
    for members in buckets.values():
        if len(members) > 80:
            continue
        for index, (left_id, left_name, left_key) in enumerate(members):
            for right_id, right_name, right_key in members[index + 1 :]:
                if left_key == right_key or len(left_key.split()) != len(right_key.split()):
                    continue
                pair_key = (*sorted((left_id, right_id)), "conservative_one_edit")
                if pair_key in seen or not _edit_distance_at_most_one(left_key, right_key):
                    continue
                seen.add(pair_key)
                inserted += int(
                    _upsert_auto_match(
                        con,
                        left=left_id,
                        right=right_id,
                        method="conservative_one_edit",
                        confidence=0.996,
                        evidence={
                            "left_name": left_name,
                            "right_name": right_name,
                            "left_normalized": left_key,
                            "right_normalized": right_key,
                            "maximum_edit_distance": 1,
                        },
                    )
                )
    return inserted


def cleanup_invalid_entity_matching(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Retire resolver material created from non-organisation values.

    Some source tables can contain dates or accounting values in a column that
    superficially looks like a supplier field.  They must never become fuzzy
    organisation-match work.  Existing bad reviews are resolved and their
    automated match candidates are rejected so they cannot reopen next cycle.
    """
    ensure_resolution_schema(con)
    invalid_ids = {
        str(entity_id)
        for entity_id, name, entity_type in con.execute(
            "SELECT entity_id,canonical_name,entity_type FROM entities"
        ).fetchall()
        if str(entity_type) in ORG_TYPES and not is_plausible_org_name(str(name))
    }
    if not invalid_ids:
        return {"invalid_aliases_removed": 0, "invalid_matches_rejected": 0, "invalid_reviews_resolved": 0}

    placeholders = ",".join("?" for _ in invalid_ids)
    params = sorted(invalid_ids)
    aliases = con.execute(
        f"DELETE FROM entity_aliases WHERE entity_id IN ({placeholders}) RETURNING alias_id",
        params,
    ).fetchall()
    matches = con.execute(
        f"""UPDATE entity_matches
            SET status='rejected',updated_at=now()
            WHERE status='review'
              AND match_method IN ('name_similarity','normalized_name_exact')
              AND (left_entity_id IN ({placeholders}) OR right_entity_id IN ({placeholders}))
            RETURNING match_id""",
        params + params,
    ).fetchall()
    reviews = con.execute(
        f"""UPDATE review_queue
            SET status='resolved',updated_at=now()
            WHERE item_type='ENTITY_MATCH'
              AND status='open'
              AND (subject_entity_id IN ({placeholders}) OR object_entity_id IN ({placeholders}))
            RETURNING review_id""",
        params + params,
    ).fetchall()
    return {
        "invalid_aliases_removed": len(aliases),
        "invalid_matches_rejected": len(matches),
        "invalid_reviews_resolved": len(reviews),
    }


def suppress_value_mismatches_with_incomplete_period_coverage(
    con: duckdb.DuckDBPyConnection,
) -> int:
    """Retire value-overrun signals when temporal contract coverage is incomplete.

    The value detector currently compares the complete supplier payment history
    with tracked contract values.  If the same supplier/buyer pair has positive
    payments outside all tracked complete contract periods, that total is not a
    like-for-like denominator comparison.  Keep the period review, but retire
    the value signal until contract coverage becomes complete enough to support
    the comparison.
    """
    rows = con.execute(
        """SELECT value.signal_id,value.metadata_json
           FROM signals value
           JOIN signals period
             ON period.subject_entity_id=value.subject_entity_id
            AND period.object_entity_id IS NOT DISTINCT FROM value.object_entity_id
           WHERE value.signal_type='PAYMENTS_EXCEED_TRACKED_CONTRACT_VALUE'
             AND value.status IN ('review','approved')
             AND period.signal_type='PAYMENTS_OUTSIDE_TRACKED_CONTRACT_PERIODS'
             AND period.status IN ('review','approved')"""
    ).fetchall()
    if not rows:
        return 0

    signal_ids: list[str] = []
    for signal_id, metadata_json in rows:
        signal_id = str(signal_id)
        try:
            metadata = json.loads(metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["suppressed_reason"] = "tracked_periods_do_not_cover_payment_stream"
        metadata["interpretation"] = "suppressed_incomplete_contract_coverage"
        con.execute(
            """UPDATE signals
               SET status='stale',metadata_json=?,updated_at=now()
               WHERE signal_id=?""",
            [json.dumps(metadata, sort_keys=True, ensure_ascii=False), signal_id],
        )
        signal_ids.append(signal_id)

    review_rows = con.execute(
        """SELECT review_id,metadata_json
           FROM review_queue
           WHERE item_type='SIGNAL_REVIEW' AND status='open'"""
    ).fetchall()
    signal_set = set(signal_ids)
    stale_reviews: list[tuple[str]] = []
    for review_id, metadata_json in review_rows:
        try:
            metadata = json.loads(metadata_json or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(metadata, dict) and str(metadata.get("signal_id") or "") in signal_set:
            stale_reviews.append((str(review_id),))
    if stale_reviews:
        con.executemany(
            "UPDATE review_queue SET status='stale',updated_at=now() WHERE review_id=?",
            stale_reviews,
        )
    return len(signal_ids)
