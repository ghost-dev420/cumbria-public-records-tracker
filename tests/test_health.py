from datetime import datetime

import httpx

from public_records_tracker.db import connect
from public_records_tracker.health import (
    classify_source_error,
    ensure_health_schema,
    finish_source_run,
    start_source_run,
)


def test_source_run_timestamps_use_one_utc_clock(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_health_schema(con)
    run_id = start_source_run(con, "source-a")
    finish_source_run(
        con,
        run_id=run_id,
        status="success",
        record_count=1,
        fact_count=1,
    )
    started, finished = con.execute(
        "SELECT started_at,finished_at FROM source_runs WHERE run_id=?", [run_id]
    ).fetchone()
    assert isinstance(started, datetime)
    assert isinstance(finished, datetime)
    assert finished >= started
    assert (finished - started).total_seconds() < 60
    con.close()


def test_httpx_transport_errors_are_network_errors():
    request = httpx.Request("GET", "https://example.test")
    error = httpx.ConnectError("temporary failure in name resolution", request=request)
    assert classify_source_error(error) == "network_error"
