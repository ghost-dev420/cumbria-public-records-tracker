from pathlib import Path

from public_records_tracker.analysis import ensure_analysis_schema
from public_records_tracker.db import connect
from public_records_tracker.procurement_gaps import detect_unmatched_payment_streams
from public_records_tracker.resolution import ensure_resolution_schema
from public_records_tracker.structured import add_fact, ensure_structured_schema, upsert_entity


def _payment(con, supplier: str, fact_value: str, key: str) -> None:
    add_fact(
        con,
        document_id=f"doc-{key}",
        snapshot_id=f"snap-{key}",
        fact_type="PAYMENT",
        predicate="PAYMENT_TO_SUPPLIER",
        evidence_class="OFFICIAL_RECORD",
        subject_entity_id=None,
        object_entity_id=supplier,
        value_text=fact_value,
        locator=f"row {key}",
        metadata={"currency": "GBP"},
    )


def test_large_unmatched_payment_stream_is_internal_review_only(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    ensure_analysis_schema(con)
    supplier = upsert_entity(con, entity_type="SUPPLIER", name="Example Services Ltd")
    _payment(con, supplier, "18000", "1")
    _payment(con, supplier, "15000", "2")

    assert detect_unmatched_payment_streams(con) == 1
    signal = con.execute(
        """SELECT signal_type,status,summary,metadata_json
           FROM signals WHERE signal_type='PAYMENT_STREAM_WITHOUT_TRACKED_PROCUREMENT_LINK'"""
    ).fetchone()
    assert signal is not None
    assert signal[1] == "review"
    assert "£33,000.00" in signal[2]
    assert '"threshold_is_legal_threshold": false' in signal[3].lower()
    review = con.execute(
        "SELECT item_type,status FROM review_queue WHERE item_type='SOURCE_DISCOVERY_REVIEW'"
    ).fetchone()
    assert review == ("SOURCE_DISCOVERY_REVIEW", "open")
    con.close()


def test_small_unmatched_payment_stream_is_not_flagged(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    ensure_analysis_schema(con)
    supplier = upsert_entity(con, entity_type="SUPPLIER", name="Small Supplier")
    _payment(con, supplier, "29999.99", "small")
    assert detect_unmatched_payment_streams(con) == 0
    con.close()
