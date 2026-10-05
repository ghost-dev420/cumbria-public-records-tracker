from __future__ import annotations

import hashlib
import json
import re
import unicodedata

import duckdb


SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    fact_id VARCHAR PRIMARY KEY,
    document_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    fact_type VARCHAR NOT NULL,
    subject_entity_id VARCHAR,
    predicate VARCHAR NOT NULL,
    object_entity_id VARCHAR,
    value_text VARCHAR,
    locator VARCHAR,
    evidence_class VARCHAR NOT NULL,
    metadata_json VARCHAR NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS facts_document_idx ON facts(document_id);
CREATE INDEX IF NOT EXISTS facts_subject_idx ON facts(subject_entity_id);
CREATE INDEX IF NOT EXISTS facts_object_idx ON facts(object_entity_id);

CREATE TABLE IF NOT EXISTS fact_snapshot_windows (
    document_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    valid_from TIMESTAMP NOT NULL,
    valid_to TIMESTAMP,
    PRIMARY KEY(document_id, snapshot_id)
);
CREATE INDEX IF NOT EXISTS fact_windows_document_idx
    ON fact_snapshot_windows(document_id, valid_from);

CREATE OR REPLACE VIEW latest_facts AS
SELECT f.*
FROM facts f
JOIN documents d
  ON d.document_id = f.document_id
 AND d.latest_snapshot_id = f.snapshot_id
WHERE f.predicate NOT LIKE 'SUPERSEDED_%';

CREATE OR REPLACE VIEW current_facts AS
SELECT f.*, w.valid_from, w.valid_to
FROM facts f
JOIN fact_snapshot_windows w
  ON w.document_id=f.document_id
 AND w.snapshot_id=f.snapshot_id
WHERE w.valid_to IS NULL
  AND f.predicate NOT LIKE 'SUPERSEDED_%';
"""


def ensure_structured_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA)


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value.casefold()


def stable_entity_id(entity_type: str, name: str, *, namespace: str = "") -> str:
    key = f"{entity_type}\0{namespace}\0{normalize_name(name)}"
    return hashlib.sha256(key.encode()).hexdigest()


def upsert_entity(
    con: duckdb.DuckDBPyConnection,
    *,
    entity_type: str,
    name: str,
    namespace: str = "",
    metadata: dict | None = None,
) -> str:
    entity_id = stable_entity_id(entity_type, name, namespace=namespace)
    con.execute(
        """INSERT INTO entities(entity_id,entity_type,canonical_name,normalized_name,metadata_json)
           VALUES (?,?,?,?,?)
           ON CONFLICT(entity_id) DO UPDATE SET
             canonical_name=excluded.canonical_name,
             normalized_name=excluded.normalized_name,
             metadata_json=excluded.metadata_json""",
        [
            entity_id,
            entity_type,
            name.strip(),
            normalize_name(name),
            json.dumps(metadata or {}, sort_keys=True, ensure_ascii=False),
        ],
    )
    return entity_id


def activate_snapshot(
    con: duckdb.DuckDBPyConnection,
    *,
    document_id: str,
    snapshot_id: str,
    observed_at: str,
) -> None:
    row = con.execute(
        """SELECT snapshot_id
           FROM fact_snapshot_windows
           WHERE document_id=? AND valid_to IS NULL""",
        [document_id],
    ).fetchone()
    if row and row[0] == snapshot_id:
        return
    con.execute(
        """UPDATE fact_snapshot_windows
           SET valid_to=?
           WHERE document_id=? AND valid_to IS NULL AND snapshot_id<>?""",
        [observed_at, document_id, snapshot_id],
    )
    con.execute(
        """INSERT INTO fact_snapshot_windows(document_id,snapshot_id,valid_from)
           VALUES (?,?,?)
           ON CONFLICT(document_id,snapshot_id) DO NOTHING""",
        [document_id, snapshot_id, observed_at],
    )


def add_fact(
    con: duckdb.DuckDBPyConnection,
    *,
    document_id: str,
    snapshot_id: str,
    fact_type: str,
    predicate: str,
    evidence_class: str,
    subject_entity_id: str | None = None,
    object_entity_id: str | None = None,
    value_text: str | None = None,
    locator: str | None = None,
    metadata: dict | None = None,
) -> str:
    identity = "\0".join(
        [
            document_id,
            snapshot_id,
            fact_type,
            subject_entity_id or "",
            predicate,
            object_entity_id or "",
            value_text or "",
            locator or "",
        ]
    )
    fact_id = hashlib.sha256(identity.encode()).hexdigest()
    con.execute(
        """INSERT INTO facts(
             fact_id,document_id,snapshot_id,fact_type,subject_entity_id,predicate,
             object_entity_id,value_text,locator,evidence_class,metadata_json
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(fact_id) DO UPDATE SET metadata_json=excluded.metadata_json""",
        [
            fact_id,
            document_id,
            snapshot_id,
            fact_type,
            subject_entity_id,
            predicate,
            object_entity_id,
            value_text,
            locator,
            evidence_class,
            json.dumps(metadata or {}, sort_keys=True, ensure_ascii=False),
        ],
    )
    return fact_id


def facts_at(
    con: duckdb.DuckDBPyConnection,
    when: str,
) -> list[tuple]:
    return con.execute(
        """SELECT f.*, w.valid_from, w.valid_to
           FROM facts f
           JOIN fact_snapshot_windows w
             ON w.document_id=f.document_id AND w.snapshot_id=f.snapshot_id
           WHERE w.valid_from <= ?
             AND (w.valid_to IS NULL OR w.valid_to > ?)
             AND f.predicate NOT LIKE 'SUPERSEDED_%'""",
        [when, when],
    ).fetchall()
