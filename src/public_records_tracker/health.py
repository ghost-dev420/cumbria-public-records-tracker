from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import duckdb


SCHEMA = """
CREATE TABLE IF NOT EXISTS source_runs (
    run_id VARCHAR PRIMARY KEY,
    source_id VARCHAR NOT NULL,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    status VARCHAR NOT NULL,
    record_count INTEGER NOT NULL DEFAULT 0,
    fact_count INTEGER NOT NULL DEFAULT 0,
    error_class VARCHAR,
    error_message VARCHAR,
    http_status INTEGER
);
CREATE INDEX IF NOT EXISTS source_runs_source_idx ON source_runs(source_id, started_at);
CREATE OR REPLACE VIEW latest_source_health AS
SELECT * EXCLUDE (rn)
FROM (
    SELECT *, row_number() OVER (PARTITION BY source_id ORDER BY started_at DESC) AS rn
    FROM source_runs
)
WHERE rn=1;
"""


def ensure_health_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA)


def start_source_run(con: duckdb.DuckDBPyConnection, source_id: str) -> str:
    ensure_health_schema(con)
    started = datetime.now(timezone.utc).isoformat()
    run_id = hashlib.sha256(f"{source_id}\0{started}".encode()).hexdigest()
    con.execute(
        """INSERT INTO source_runs(run_id,source_id,started_at,status)
           VALUES (?,?,?,'running')""",
        [run_id, source_id, started],
    )
    return run_id


def finish_source_run(
    con: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    status: str,
    record_count: int,
    fact_count: int,
    error: Exception | None = None,
) -> None:
    http_status = None
    if error is not None:
        response = getattr(error, "response", None)
        http_status = getattr(response, "status_code", None)
    con.execute(
        """UPDATE source_runs
           SET finished_at=now(), status=?, record_count=?, fact_count=?,
               error_class=?, error_message=?, http_status=?
           WHERE run_id=?""",
        [
            status,
            record_count,
            fact_count,
            error.__class__.__name__ if error else None,
            str(error)[:4000] if error else None,
            http_status,
            run_id,
        ],
    )


def classify_source_error(error: Exception) -> str:
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if status in {401, 403}:
        return "blocked"
    if status == 404:
        return "missing"
    if status == 429:
        return "rate_limited"
    if status is not None:
        return "http_error"
    if isinstance(error, OSError):
        return "network_error"
    return "error"
