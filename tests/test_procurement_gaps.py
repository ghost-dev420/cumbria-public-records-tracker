from pathlib import Path

from public_records_tracker.analysis import ensure_analysis_schema
from public_records_tracker.db import connect
from public_records_tracker.procurement_gaps import detect_unmatched_payment_streams
from public_records_tracker.resolution import ensure_resolution_schema
from public_records_tracker.structured import add_fact, ensure_structured_schema, upsert_entity


def _payment(con, supplier: str, fact_value: str, key: str) -> None:
    document_id = f"doc-{key}"
    snapshot_id = f"snap-{key}"
    con.execute(
        """INSERT INTO snapshots(
             snapshot_id,source_id,canonical_url,retrieved_at,sha256,archive_path,
             content_type,status_code,etag,last_modified
           ) VALUES (?,?,?,now(),?,?,?,200,NULL,NULL)""",
        [snapshot_id, "test-source", f"https://example.gov/payments/{key}", f"sha-{key}", "raw", "text/csv"],
    )
    con.execute(
        """INSERT INTO documents(
             document_id,source_id,canonical_url,title,published_at,evidence_class,
             latest_snapshot_id,metadata_json
           ) VALUES (?,?,?,?,?,'OFFICIAL_RECORD',?,'{}')""",
        [
            document_id,
            "test-source",
            f"https://example.gov/payments/{key}",
            f"Payments {key}",
            "2026-09-01",
            snapshot_id,
        ],
    )
    add_fact(
        con,
        document_id=document_id,
        snapshot_id=snapshot_id,
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


def test_town_council_abbreviation_is_not_a_procurement_gap(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    ensure_analysis_schema(con)
    council_payee = upsert_entity(
        con, entity_type="SUPPLIER", name="WORKINGTON T.C."
    )
    _payment(con, council_payee, "315725.00", "tc")

    assert detect_unmatched_payment_streams(con) == 0
    assert con.execute("SELECT count(*) FROM signals").fetchone()[0] == 0
    con.close()


def test_bare_legacy_authority_alias_is_not_a_procurement_gap(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    ensure_analysis_schema(con)
    con.execute(
        """INSERT INTO organisations(
             organisation_id,name,organisation_type,status,jurisdiction,
             valid_from,valid_to,config_json
           ) VALUES (
             'allerdale_borough_council','Allerdale Borough Council',
             'district_council','legacy','Allerdale',NULL,'2023-03-31','{}'
           )"""
    )
    legacy_payee = upsert_entity(con, entity_type="SUPPLIER", name="ALLERDALE")
    _payment(con, legacy_payee, "767857.71", "legacy")

    assert detect_unmatched_payment_streams(con) == 0
    assert con.execute("SELECT count(*) FROM signals").fetchone()[0] == 0
    con.close()
