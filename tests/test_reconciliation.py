import json

from public_records_tracker.analysis import ensure_analysis_schema
from public_records_tracker.db import connect
from public_records_tracker.extractors.contracts_periods import extract_contract_periods
from public_records_tracker.models import EvidenceClass, Record
from public_records_tracker.reconciliation import run_reconciliation_detectors
from public_records_tracker.resolution import ensure_resolution_schema
from public_records_tracker.structured import add_fact, ensure_structured_schema, upsert_entity


def _record(payload: dict) -> Record:
    return Record(
        source_id="contracts_finder",
        url="https://example.test/notice",
        title="Example contract",
        body=json.dumps(payload).encode("utf-8"),
        content_type="application/json",
        evidence_class=EvidenceClass.OFFICIAL_RECORD,
    )


def test_extracts_tender_contract_period_for_award(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    payload = {
        "ocid": "ocds-test-1",
        "tender": {
            "title": "Bridge maintenance",
            "contractPeriod": {
                "startDate": "2026-01-01T00:00:00Z",
                "endDate": "2026-03-31T23:59:59Z",
            },
        },
        "awards": [{"id": "award-1", "title": "Bridge maintenance"}],
    }
    added = extract_contract_periods(
        con=con,
        record=_record(payload),
        document_id="doc-1",
        snapshot_id="snap-1",
        source={},
    )
    assert added == 2
    rows = con.execute(
        "SELECT predicate,value_text FROM facts ORDER BY predicate"
    ).fetchall()
    assert rows == [
        ("ENDS_ON", "2026-03-31T23:59:59Z"),
        ("STARTS_ON", "2026-01-01T00:00:00Z"),
    ]
    con.close()


def test_value_and_period_mismatches_are_review_only_signals(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    ensure_analysis_schema(con)

    supplier = upsert_entity(
        con, entity_type="SUPPLIER", name="Example Engineering Ltd", namespace="test"
    )
    contract = upsert_entity(
        con, entity_type="CONTRACT", name="ocds-test:award-1", namespace="test"
    )
    con.execute(
        """INSERT INTO snapshots(
             snapshot_id,source_id,canonical_url,retrieved_at,sha256,archive_path,
             content_type,status_code,etag,last_modified
           ) VALUES (
             'snap-1','test','https://example.test/source',now(),
             'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
             'raw.json','application/json',200,NULL,NULL
           )"""
    )
    con.execute(
        """INSERT INTO documents(
             document_id,source_id,canonical_url,title,published_at,evidence_class,
             latest_snapshot_id,metadata_json
           ) VALUES (
             'doc-1','test','https://example.test/source','Source',NULL,
             'OFFICIAL_RECORD','snap-1','{}'
           )"""
    )

    add_fact(
        con,
        document_id="doc-1",
        snapshot_id="snap-1",
        fact_type="PAYMENT",
        predicate="PAYMENT_TO_SUPPLIER",
        evidence_class="OFFICIAL_RECORD",
        object_entity_id=supplier,
        value_text="12000.00",
        locator="CSV row 2",
        metadata={"payment_date": "2026-05-15", "currency": "GBP"},
    )
    add_fact(
        con,
        document_id="doc-1",
        snapshot_id="snap-1",
        fact_type="CONTRACT_SUPPLIER",
        predicate="SUPPLIER_TO_CONTRACT",
        evidence_class="OFFICIAL_RECORD",
        subject_entity_id=supplier,
        object_entity_id=contract,
        locator="award supplier",
    )
    add_fact(
        con,
        document_id="doc-1",
        snapshot_id="snap-1",
        fact_type="CONTRACT_VALUE",
        predicate="HAS_VALUE",
        evidence_class="OFFICIAL_RECORD",
        subject_entity_id=contract,
        value_text="10000.00",
        locator="award value",
        metadata={"currency": "GBP"},
    )
    add_fact(
        con,
        document_id="doc-1",
        snapshot_id="snap-1",
        fact_type="CONTRACT_PERIOD",
        predicate="STARTS_ON",
        evidence_class="OFFICIAL_RECORD",
        subject_entity_id=contract,
        value_text="2026-01-01",
        locator="period start",
    )
    add_fact(
        con,
        document_id="doc-1",
        snapshot_id="snap-1",
        fact_type="CONTRACT_PERIOD",
        predicate="ENDS_ON",
        evidence_class="OFFICIAL_RECORD",
        subject_entity_id=contract,
        value_text="2026-03-31",
        locator="period end",
    )

    stats = run_reconciliation_detectors(con)
    assert stats["payment_contract_value_or_period_review"] == 2
    signals = con.execute(
        "SELECT signal_type,status,metadata_json FROM signals ORDER BY signal_type"
    ).fetchall()
    assert [row[0] for row in signals] == [
        "PAYMENTS_EXCEED_TRACKED_CONTRACT_VALUE",
        "PAYMENTS_OUTSIDE_TRACKED_CONTRACT_PERIODS",
    ]
    assert all(row[1] == "review" for row in signals)
    assert all("review_required_not_a_finding" in row[2] for row in signals)
    assert con.execute(
        "SELECT count(*) FROM review_queue WHERE item_type='SIGNAL_REVIEW'"
    ).fetchone()[0] == 2
    con.close()
