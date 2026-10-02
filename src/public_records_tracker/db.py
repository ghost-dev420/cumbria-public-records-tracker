from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

from .archive import ArchivedSnapshot
from .models import Record


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    source_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    evidence_class VARCHAR NOT NULL,
    enabled BOOLEAN NOT NULL,
    config_json VARCHAR NOT NULL,
    updated_at TIMESTAMP DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS organisations (
    organisation_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    organisation_type VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    jurisdiction VARCHAR,
    valid_from VARCHAR,
    valid_to VARCHAR,
    config_json VARCHAR NOT NULL,
    updated_at TIMESTAMP DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS source_organisations (
    source_id VARCHAR NOT NULL,
    organisation_id VARCHAR NOT NULL,
    relationship VARCHAR NOT NULL DEFAULT 'about',
    PRIMARY KEY(source_id, organisation_id, relationship)
);
CREATE TABLE IF NOT EXISTS documents (
    document_id VARCHAR PRIMARY KEY,
    source_id VARCHAR NOT NULL,
    canonical_url VARCHAR NOT NULL,
    title VARCHAR NOT NULL,
    published_at VARCHAR,
    evidence_class VARCHAR NOT NULL,
    latest_snapshot_id VARCHAR NOT NULL,
    metadata_json VARCHAR NOT NULL,
    first_seen TIMESTAMP DEFAULT current_timestamp,
    last_seen TIMESTAMP DEFAULT current_timestamp,
    UNIQUE(source_id, canonical_url)
);
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id VARCHAR PRIMARY KEY,
    source_id VARCHAR NOT NULL,
    canonical_url VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    sha256 VARCHAR NOT NULL,
    archive_path VARCHAR NOT NULL,
    content_type VARCHAR,
    status_code INTEGER,
    etag VARCHAR,
    last_modified VARCHAR
);
CREATE TABLE IF NOT EXISTS observations (
    observation_id VARCHAR PRIMARY KEY,
    snapshot_id VARCHAR NOT NULL,
    source_id VARCHAR NOT NULL,
    canonical_url VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    status_code INTEGER,
    etag VARCHAR,
    last_modified VARCHAR,
    observation_path VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS changes (
    change_id VARCHAR PRIMARY KEY,
    source_id VARCHAR NOT NULL,
    canonical_url VARCHAR NOT NULL,
    change_type VARCHAR NOT NULL,
    old_sha256 VARCHAR,
    new_sha256 VARCHAR,
    detected_at TIMESTAMP DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS entities (
    entity_id VARCHAR PRIMARY KEY,
    entity_type VARCHAR NOT NULL,
    canonical_name VARCHAR NOT NULL,
    normalized_name VARCHAR NOT NULL,
    metadata_json VARCHAR NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS document_entities (
    document_id VARCHAR NOT NULL,
    entity_id VARCHAR NOT NULL,
    role VARCHAR NOT NULL,
    locator VARCHAR,
    confidence DOUBLE DEFAULT 1.0,
    PRIMARY KEY(document_id, entity_id, role)
);
CREATE TABLE IF NOT EXISTS relationships (
    relationship_id VARCHAR PRIMARY KEY,
    subject_entity_id VARCHAR NOT NULL,
    predicate VARCHAR NOT NULL,
    object_entity_id VARCHAR NOT NULL,
    document_id VARCHAR NOT NULL,
    locator VARCHAR,
    evidence_class VARCHAR NOT NULL,
    metadata_json VARCHAR NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS claims (
    claim_id VARCHAR PRIMARY KEY,
    claim_text VARCHAR NOT NULL,
    classification VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'open',
    created_at TIMESTAMP DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS claim_evidence (
    claim_id VARCHAR NOT NULL,
    document_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    locator VARCHAR,
    support_type VARCHAR NOT NULL,
    PRIMARY KEY(claim_id, document_id, snapshot_id, support_type)
);
CREATE TABLE IF NOT EXISTS events (
    event_id VARCHAR PRIMARY KEY,
    event_date VARCHAR,
    event_type VARCHAR NOT NULL,
    title VARCHAR NOT NULL,
    document_id VARCHAR NOT NULL,
    entity_id VARCHAR,
    evidence_class VARCHAR NOT NULL,
    metadata_json VARCHAR NOT NULL DEFAULT '{}'
);
"""


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    return con


def register_source(con: duckdb.DuckDBPyConnection, source: dict) -> None:
    con.execute(
        """INSERT INTO sources(source_id,name,kind,evidence_class,enabled,config_json)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(source_id) DO UPDATE SET
             name=excluded.name, kind=excluded.kind, evidence_class=excluded.evidence_class,
             enabled=excluded.enabled, config_json=excluded.config_json, updated_at=now()""",
        [
            source["id"], source.get("name", source["id"]), source["kind"],
            source["evidence_class"], bool(source.get("enabled", True)),
            json.dumps(source, sort_keys=True),
        ],
    )


def register_organisation(con: duckdb.DuckDBPyConnection, organisation: dict) -> None:
    con.execute(
        """INSERT INTO organisations(
             organisation_id,name,organisation_type,status,jurisdiction,valid_from,valid_to,config_json
           ) VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(organisation_id) DO UPDATE SET
             name=excluded.name, organisation_type=excluded.organisation_type,
             status=excluded.status, jurisdiction=excluded.jurisdiction,
             valid_from=excluded.valid_from, valid_to=excluded.valid_to,
             config_json=excluded.config_json, updated_at=now()""",
        [
            organisation["id"], organisation["name"], organisation["organisation_type"],
            organisation.get("status", "current"), organisation.get("jurisdiction"),
            organisation.get("valid_from"), organisation.get("valid_to"),
            json.dumps(organisation, sort_keys=True),
        ],
    )


def link_source_organisations(con: duckdb.DuckDBPyConnection, source: dict) -> None:
    con.execute("DELETE FROM source_organisations WHERE source_id=?", [source["id"]])
    for organisation_id in source.get("organisation_ids", []):
        con.execute(
            """INSERT INTO source_organisations(source_id,organisation_id,relationship)
               VALUES (?,?,?) ON CONFLICT DO NOTHING""",
            [source["id"], organisation_id, "about"],
        )


def ingest(con: duckdb.DuckDBPyConnection, record: Record, snap: ArchivedSnapshot) -> str:
    doc_id = hashlib.sha256(
        f"{record.source_id}\0{snap.canonical_url}".encode("utf-8")
    ).hexdigest()
    row = con.execute(
        "SELECT latest_snapshot_id FROM documents WHERE document_id=?", [doc_id]
    ).fetchone()
    old_hash = None
    if row:
        old = con.execute("SELECT sha256 FROM snapshots WHERE snapshot_id=?", [row[0]]).fetchone()
        old_hash = old[0] if old else None

    con.execute(
        """INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(snapshot_id) DO NOTHING""",
        [snap.snapshot_id, record.source_id, snap.canonical_url, snap.retrieved_at, snap.sha256,
         snap.archive_path, record.content_type, record.status_code, record.etag, record.last_modified],
    )
    con.execute(
        """INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(observation_id) DO NOTHING""",
        [snap.observation_id, snap.snapshot_id, record.source_id, snap.canonical_url,
         snap.retrieved_at, record.status_code, record.etag, record.last_modified,
         snap.observation_path],
    )
    con.execute(
        """INSERT INTO documents(document_id,source_id,canonical_url,title,published_at,evidence_class,
             latest_snapshot_id,metadata_json)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(document_id) DO UPDATE SET title=excluded.title,
             published_at=coalesce(excluded.published_at,documents.published_at),
             evidence_class=excluded.evidence_class, latest_snapshot_id=excluded.latest_snapshot_id,
             metadata_json=excluded.metadata_json, last_seen=now()""",
        [doc_id, record.source_id, snap.canonical_url, record.title, record.published_at,
         record.evidence_class.value, snap.snapshot_id, json.dumps(record.metadata, sort_keys=True)],
    )
    if old_hash and old_hash != snap.sha256:
        change_id = hashlib.sha256(
            f"{record.source_id}\0{snap.canonical_url}\0{old_hash}\0{snap.sha256}".encode()
        ).hexdigest()
        con.execute(
            """INSERT INTO changes(change_id,source_id,canonical_url,change_type,old_sha256,new_sha256)
               VALUES (?,?,?,?,?,?) ON CONFLICT(change_id) DO NOTHING""",
            [change_id, record.source_id, snap.canonical_url, "CONTENT_CHANGED", old_hash, snap.sha256],
        )
    return doc_id


def dashboard_data(con: duckdb.DuckDBPyConnection) -> dict:
    counts = dict(con.execute(
        "SELECT 'documents', count(*) FROM documents UNION ALL SELECT 'snapshots', count(*) FROM snapshots "
        "UNION ALL SELECT 'observations', count(*) FROM observations "
        "UNION ALL SELECT 'changes', count(*) FROM changes "
        "UNION ALL SELECT 'organisations', count(*) FROM organisations "
        "UNION ALL SELECT 'entities', count(*) FROM entities"
    ).fetchall())
    recent_docs = con.execute(
        """SELECT title, source_id, canonical_url, evidence_class, published_at, last_seen
           FROM documents ORDER BY last_seen DESC LIMIT 100"""
    ).fetchall()
    recent_changes = con.execute(
        """SELECT source_id, canonical_url, change_type, old_sha256, new_sha256, detected_at
           FROM changes ORDER BY detected_at DESC LIMIT 100"""
    ).fetchall()
    return {"counts": counts, "recent_docs": recent_docs, "recent_changes": recent_changes}
