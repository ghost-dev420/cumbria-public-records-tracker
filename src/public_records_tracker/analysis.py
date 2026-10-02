from __future__ import annotations

import hashlib
import json

import duckdb

from .resolution import ensure_resolution_schema, put_review


SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    signal_id VARCHAR PRIMARY KEY,
    signal_type VARCHAR NOT NULL,
    score DOUBLE NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'review',
    summary VARCHAR NOT NULL,
    subject_entity_id VARCHAR,
    object_entity_id VARCHAR,
    evidence_json VARCHAR NOT NULL DEFAULT '[]',
    metadata_json VARCHAR NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT current_timestamp,
    updated_at TIMESTAMP DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS signals_type_idx ON signals(signal_type);
CREATE INDEX IF NOT EXISTS signals_status_idx ON signals(status);
"""


def ensure_analysis_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA)


def _signal_id(
    signal_type: str,
    subject: str | None,
    object_: str | None,
    evidence: list[str],
) -> str:
    key = "\0".join([signal_type, subject or "", object_ or "", *sorted(evidence)])
    return hashlib.sha256(key.encode()).hexdigest()


def add_signal(
    con: duckdb.DuckDBPyConnection,
    *,
    signal_type: str,
    score: float,
    summary: str,
    subject: str | None,
    object_: str | None,
    evidence: list[str],
    metadata: dict | None = None,
) -> str:
    ensure_analysis_schema(con)
    signal_id = _signal_id(signal_type, subject, object_, evidence)
    con.execute(
        """INSERT INTO signals(
             signal_id,signal_type,score,summary,subject_entity_id,object_entity_id,
             evidence_json,metadata_json
           ) VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(signal_id) DO UPDATE SET
             score=excluded.score,
             summary=excluded.summary,
             evidence_json=excluded.evidence_json,
             metadata_json=excluded.metadata_json,
             updated_at=now()""",
        [
            signal_id,
            signal_type,
            score,
            summary,
            subject,
            object_,
            json.dumps(sorted(evidence), ensure_ascii=False),
            json.dumps(metadata or {}, sort_keys=True, ensure_ascii=False),
        ],
    )
    return signal_id


def detect_shared_outside_bodies(con: duckdb.DuckDBPyConnection) -> int:
    rows = con.execute(
        """SELECT object_entity_id,
                  list(DISTINCT subject_entity_id),
                  list(DISTINCT fact_id)
           FROM latest_facts
           WHERE predicate='APPOINTED_TO_OUTSIDE_BODY'
             AND subject_entity_id IS NOT NULL
             AND object_entity_id IS NOT NULL
           GROUP BY object_entity_id
           HAVING count(DISTINCT subject_entity_id) >= 2"""
    ).fetchall()
    count = 0
    for body_id, person_ids, fact_ids in rows:
        names = con.execute(
            "SELECT canonical_name FROM entities WHERE entity_id=?", [body_id]
        ).fetchone()
        name = names[0] if names else body_id
        n = len(set(person_ids))
        score = min(0.55, 0.25 + 0.08 * (n - 2))
        add_signal(
            con,
            signal_type="SHARED_OUTSIDE_BODY",
            score=score,
            summary=f"{n} recorded office-holders are appointed to the same outside body: {name}",
            subject=None,
            object_=body_id,
            evidence=list(set(fact_ids)),
            metadata={"person_entity_ids": sorted(set(person_ids)), "count": n},
        )
        count += 1
    return count


def detect_declared_interest_supplier_overlap(con: duckdb.DuckDBPyConnection) -> int:
    ensure_resolution_schema(con)
    reviews = con.execute(
        """SELECT review_id,subject_entity_id,object_entity_id,evidence_json,metadata_json
           FROM review_queue
           WHERE item_type='DECLARED_INTEREST_ENTITY_CANDIDATE'
             AND status='open'"""
    ).fetchall()
    count = 0
    for review_id, person_id, supplier_id, evidence_json, metadata_json in reviews:
        contract_facts = con.execute(
            """SELECT fact_id,object_entity_id
               FROM latest_facts
               WHERE predicate='SUPPLIER_TO_CONTRACT'
                 AND subject_entity_id=?""",
            [supplier_id],
        ).fetchall()
        if not contract_facts:
            continue
        try:
            evidence = list(json.loads(evidence_json or "[]"))
        except json.JSONDecodeError:
            evidence = []
        evidence.extend(row[0] for row in contract_facts)
        try:
            review_meta = json.loads(metadata_json or "{}")
        except json.JSONDecodeError:
            review_meta = {}
        supplier_name = con.execute(
            "SELECT canonical_name FROM entities WHERE entity_id=?", [supplier_id]
        ).fetchone()
        name = supplier_name[0] if supplier_name else supplier_id
        add_signal(
            con,
            signal_type="DECLARED_INTEREST_SUPPLIER_OVERLAP",
            score=0.68,
            summary=(
                "A declared-interest entry may refer to an entity also recorded as a "
                f"contract supplier: {name}"
            ),
            subject=person_id,
            object_=supplier_id,
            evidence=sorted(set(evidence)),
            metadata={
                "review_id": review_id,
                "candidate_match": review_meta,
                "contract_entity_ids": sorted({row[1] for row in contract_facts}),
                "interpretation": "review_required_not_a_finding",
            },
        )
        put_review(
            con,
            item_type="SIGNAL_REVIEW",
            score=0.68,
            summary=f"Review declared-interest / supplier overlap for {name}",
            subject=person_id,
            object_=supplier_id,
            evidence=sorted(set(evidence)),
            metadata={"signal_type": "DECLARED_INTEREST_SUPPLIER_OVERLAP"},
        )
        count += 1
    return count


def run_detectors(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    ensure_analysis_schema(con)
    return {
        "shared_outside_bodies": detect_shared_outside_bodies(con),
        "declared_interest_supplier_overlap": detect_declared_interest_supplier_overlap(con),
    }
