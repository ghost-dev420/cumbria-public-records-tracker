from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path

import duckdb

from .db import connect


SCHEMA = """
CREATE TABLE IF NOT EXISTS structured_changes (
    change_id VARCHAR PRIMARY KEY,
    document_id VARCHAR NOT NULL,
    old_snapshot_id VARCHAR NOT NULL,
    new_snapshot_id VARCHAR NOT NULL,
    change_type VARCHAR NOT NULL,
    fact_type VARCHAR NOT NULL,
    predicate VARCHAR NOT NULL,
    subject_entity_id VARCHAR,
    object_entity_id VARCHAR,
    value_text VARCHAR,
    locator VARCHAR,
    old_fact_id VARCHAR,
    new_fact_id VARCHAR,
    detected_at TIMESTAMP DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS structured_changes_document_idx
    ON structured_changes(document_id, detected_at);
CREATE INDEX IF NOT EXISTS structured_changes_type_idx
    ON structured_changes(change_type, detected_at);
"""


def ensure_diff_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA)


def _fact_rows(con: duckdb.DuckDBPyConnection, document_id: str, snapshot_id: str) -> dict[tuple, str]:
    rows = con.execute(
        """SELECT fact_id,fact_type,predicate,subject_entity_id,object_entity_id,
                  value_text,locator
           FROM facts
           WHERE document_id=? AND snapshot_id=?""",
        [document_id, snapshot_id],
    ).fetchall()
    return {
        (
            fact_type,
            predicate,
            subject_entity_id,
            object_entity_id,
            value_text,
            locator,
        ): fact_id
        for fact_id, fact_type, predicate, subject_entity_id, object_entity_id, value_text, locator in rows
    }


def _change_id(
    document_id: str,
    old_snapshot_id: str,
    new_snapshot_id: str,
    change_type: str,
    signature: tuple,
) -> str:
    key = "\0".join(
        [
            document_id,
            old_snapshot_id,
            new_snapshot_id,
            change_type,
            *("" if value is None else str(value) for value in signature),
        ]
    )
    return hashlib.sha256(key.encode()).hexdigest()


def record_structured_diff(
    con: duckdb.DuckDBPyConnection,
    *,
    document_id: str,
    new_snapshot_id: str,
) -> int:
    ensure_diff_schema(con)
    previous = con.execute(
        """SELECT snapshot_id
           FROM fact_snapshot_windows
           WHERE document_id=? AND snapshot_id<>?
           ORDER BY valid_from DESC
           LIMIT 1""",
        [document_id, new_snapshot_id],
    ).fetchone()
    if previous is None:
        return 0
    old_snapshot_id = previous[0]
    old_rows = _fact_rows(con, document_id, old_snapshot_id)
    new_rows = _fact_rows(con, document_id, new_snapshot_id)
    count = 0

    for signature in sorted(old_rows.keys() - new_rows.keys(), key=str):
        fact_type, predicate, subject, object_, value, locator = signature
        change_id = _change_id(
            document_id, old_snapshot_id, new_snapshot_id, "FACT_REMOVED", signature
        )
        con.execute(
            """INSERT INTO structured_changes(
                 change_id,document_id,old_snapshot_id,new_snapshot_id,change_type,
                 fact_type,predicate,subject_entity_id,object_entity_id,value_text,
                 locator,old_fact_id,new_fact_id
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(change_id) DO NOTHING""",
            [
                change_id,
                document_id,
                old_snapshot_id,
                new_snapshot_id,
                "FACT_REMOVED",
                fact_type,
                predicate,
                subject,
                object_,
                value,
                locator,
                old_rows[signature],
                None,
            ],
        )
        count += 1

    for signature in sorted(new_rows.keys() - old_rows.keys(), key=str):
        fact_type, predicate, subject, object_, value, locator = signature
        change_id = _change_id(
            document_id, old_snapshot_id, new_snapshot_id, "FACT_ADDED", signature
        )
        con.execute(
            """INSERT INTO structured_changes(
                 change_id,document_id,old_snapshot_id,new_snapshot_id,change_type,
                 fact_type,predicate,subject_entity_id,object_entity_id,value_text,
                 locator,old_fact_id,new_fact_id
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(change_id) DO NOTHING""",
            [
                change_id,
                document_id,
                old_snapshot_id,
                new_snapshot_id,
                "FACT_ADDED",
                fact_type,
                predicate,
                subject,
                object_,
                value,
                locator,
                None,
                new_rows[signature],
            ],
        )
        count += 1
    return count


def structured_change_rows(con: duckdb.DuckDBPyConnection, limit: int = 500) -> list[dict]:
    ensure_diff_schema(con)
    cursor = con.execute(
        """SELECT c.change_id,c.change_type,c.fact_type,c.predicate,
                  se.canonical_name AS subject_name,oe.canonical_name AS object_name,
                  c.value_text,c.locator,c.old_snapshot_id,c.new_snapshot_id,
                  old_s.sha256 AS old_sha256,new_s.sha256 AS new_sha256,
                  d.canonical_url AS source_url,c.detected_at
           FROM structured_changes c
           JOIN documents d ON d.document_id=c.document_id
           LEFT JOIN entities se ON se.entity_id=c.subject_entity_id
           LEFT JOIN entities oe ON oe.entity_id=c.object_entity_id
           LEFT JOIN snapshots old_s ON old_s.snapshot_id=c.old_snapshot_id
           LEFT JOIN snapshots new_s ON new_s.snapshot_id=c.new_snapshot_id
           ORDER BY c.detected_at DESC,c.change_id
           LIMIT ?""",
        [limit],
    )
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _link_from_index(out_dir: Path) -> None:
    index_path = out_dir / "index.html"
    if not index_path.exists():
        return
    page = index_path.read_text(encoding="utf-8")
    if "structured-changes.html" in page:
        return
    marker = "<a class='button' href='api/index.json'>API</a>"
    link = "<a class='button' href='structured-changes.html'>What changed</a>"
    if marker in page:
        page = page.replace(marker, marker + link, 1)
        index_path.write_text(page, encoding="utf-8")


def build_structured_changes(db_path: Path, out_dir: Path) -> None:
    con = connect(db_path)
    rows = structured_change_rows(con)
    con.close()

    api_dir = out_dir / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "structured-changes.json").write_text(
        json.dumps(rows, default=str, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    table_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row['change_type']))}</td>"
        f"<td>{html.escape(str(row['fact_type']))}</td>"
        f"<td>{html.escape(str(row['predicate']))}</td>"
        f"<td>{html.escape(str(row['subject_name'] or ''))}</td>"
        f"<td>{html.escape(str(row['object_name'] or ''))}</td>"
        f"<td>{html.escape(str(row['value_text'] or ''))}</td>"
        f"<td>{html.escape(str(row['locator'] or ''))}</td>"
        f"<td><a rel='noreferrer' href='{html.escape(str(row['source_url']), quote=True)}'>source</a></td>"
        f"<td><code>{html.escape(str(row['old_sha256'] or ''))}</code><br><code>{html.escape(str(row['new_sha256'] or ''))}</code></td>"
        "</tr>"
        for row in rows
    ) or "<tr><td colspan='9'>No structured fact changes recorded yet.</td></tr>"

    page = f"""<!doctype html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>What changed — Cumbria Public Records Evidence Tracker</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:1500px;margin:auto;padding:2rem;color:#171717}}
table{{width:100%;border-collapse:collapse;font-size:.88rem}}th,td{{padding:.55rem;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}}
code{{font-size:.72rem;overflow-wrap:anywhere}}.note{{background:#f6f7f8;border:1px solid #ddd;border-radius:10px;padding:1rem}}
</style>
</head>
<body>
<p><a href='index.html'>← Main tracker</a></p>
<h1>What changed in structured records</h1>
<p class='note'>Added and removed facts are derived by comparing two archived snapshots of the same public record. A removal means the fact was not extracted from the newer snapshot; it does not by itself establish why the underlying record changed.</p>
<table>
<thead><tr><th>Change</th><th>Fact type</th><th>Predicate</th><th>Subject</th><th>Entity</th><th>Value</th><th>Locator</th><th>Source</th><th>Old / new SHA-256</th></tr></thead>
<tbody>{table_rows}</tbody>
</table>
</body>
</html>"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "structured-changes.html").write_text(page, encoding="utf-8")
    _link_from_index(out_dir)
