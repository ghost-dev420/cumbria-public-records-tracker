from public_records_tracker.analysis import add_signal, ensure_analysis_schema
from public_records_tracker.analysis_lifecycle import (
    begin_analysis_cycle,
    finish_analysis_cycle,
)
from public_records_tracker.db import connect
from public_records_tracker.resolution import ensure_resolution_schema, put_review


def test_analysis_cycle_retires_unreproduced_approved_signal(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_analysis_schema(con)
    ensure_resolution_schema(con)
    signal_id = add_signal(
        con,
        signal_type="TEST_SIGNAL",
        score=0.5,
        summary="Old signal",
        subject=None,
        object_=None,
        evidence=["fact-old"],
    )
    con.execute("UPDATE signals SET status='approved', updated_at=updated_at - INTERVAL 1 DAY")

    started = begin_analysis_cycle(con)
    stats = finish_analysis_cycle(con, started)
    assert stats["stale_signals"] == 1
    assert con.execute(
        "SELECT status FROM signals WHERE signal_id=?", [signal_id]
    ).fetchone()[0] == "stale"
    con.close()


def test_analysis_cycle_preserves_reproduced_approved_signal(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_analysis_schema(con)
    ensure_resolution_schema(con)
    signal_id = add_signal(
        con,
        signal_type="TEST_SIGNAL",
        score=0.5,
        summary="Signal",
        subject=None,
        object_=None,
        evidence=["fact-current"],
    )
    con.execute("UPDATE signals SET status='approved', updated_at=updated_at - INTERVAL 1 DAY")

    started = begin_analysis_cycle(con)
    add_signal(
        con,
        signal_type="TEST_SIGNAL",
        score=0.5,
        summary="Signal",
        subject=None,
        object_=None,
        evidence=["fact-current"],
    )
    stats = finish_analysis_cycle(con, started)
    assert stats["stale_signals"] == 0
    assert con.execute(
        "SELECT status FROM signals WHERE signal_id=?", [signal_id]
    ).fetchone()[0] == "approved"
    con.close()


def test_analysis_cycle_retires_open_review_not_reproduced(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_analysis_schema(con)
    ensure_resolution_schema(con)
    review_id = put_review(
        con,
        item_type="SIGNAL_REVIEW",
        score=0.5,
        summary="Old review",
        subject=None,
        object_=None,
        evidence=["fact-old"],
    )
    con.execute("UPDATE review_queue SET updated_at=updated_at - INTERVAL 1 DAY")
    started = begin_analysis_cycle(con)
    stats = finish_analysis_cycle(con, started)
    assert stats["stale_reviews"] == 1
    assert con.execute(
        "SELECT status FROM review_queue WHERE review_id=?", [review_id]
    ).fetchone()[0] == "stale"
    con.close()


def test_analysis_cycle_retires_recent_item_during_bst(tmp_path):
    """A 30-minute-old item must retire even when the DB session is on UK BST.

    The old implementation used Python UTC-naive time for the cycle boundary
    while DuckDB stored local naive timestamps. During BST that made a recently
    unsupported item appear up to an hour newer than the cycle start.
    """
    con = connect(tmp_path / "tracker.duckdb")
    con.execute("SET TimeZone='Europe/London'")
    ensure_analysis_schema(con)
    ensure_resolution_schema(con)

    signal_id = add_signal(
        con,
        signal_type="BST_TEST_SIGNAL",
        score=0.5,
        summary="Recent but unsupported signal",
        subject=None,
        object_=None,
        evidence=["fact-bst"],
    )
    review_id = put_review(
        con,
        item_type="BST_TEST_REVIEW",
        score=0.5,
        summary="Recent but unsupported review",
        subject=None,
        object_=None,
        evidence=["fact-bst"],
    )
    con.execute(
        """UPDATE signals
           SET updated_at=CAST(current_timestamp AS TIMESTAMP) - INTERVAL 30 MINUTE
           WHERE signal_id=?""",
        [signal_id],
    )
    con.execute(
        """UPDATE review_queue
           SET updated_at=CAST(current_timestamp AS TIMESTAMP) - INTERVAL 30 MINUTE
           WHERE review_id=?""",
        [review_id],
    )

    started = begin_analysis_cycle(con)
    stats = finish_analysis_cycle(con, started)

    assert stats["stale_signals"] == 1
    assert stats["stale_reviews"] == 1
    assert con.execute(
        "SELECT status FROM signals WHERE signal_id=?", [signal_id]
    ).fetchone()[0] == "stale"
    assert con.execute(
        "SELECT status FROM review_queue WHERE review_id=?", [review_id]
    ).fetchone()[0] == "stale"
    con.close()
