from public_records_tracker.analysis import ensure_analysis_schema
from public_records_tracker.db import connect
from public_records_tracker.reconciliation import detect_payment_contract_value_mismatch
from public_records_tracker.resolution import ensure_resolution_schema
from public_records_tracker.structured import add_fact, ensure_structured_schema, upsert_entity


def _base(con):
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    ensure_analysis_schema(con)
    con.execute(
        """INSERT INTO snapshots(
             snapshot_id,source_id,canonical_url,retrieved_at,sha256,archive_path,
             content_type,status_code,etag,last_modified
           ) VALUES (
             'snap','test','https://example.test/source',now(),
             'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
             'raw.json','application/json',200,NULL,NULL
           )"""
    )
    con.execute(
        """INSERT INTO documents(
             document_id,source_id,canonical_url,title,published_at,evidence_class,
             latest_snapshot_id,metadata_json
           ) VALUES (
             'doc','test','https://example.test/source','Source',NULL,
             'OFFICIAL_RECORD','snap','{}'
           )"""
    )


def _entity(con, entity_type, name):
    return upsert_entity(con, entity_type=entity_type, name=name, namespace="test")


def _payment(con, payer, supplier, amount, payment_date, locator):
    add_fact(
        con,
        document_id="doc",
        snapshot_id="snap",
        fact_type="PAYMENT",
        predicate="PAYMENT_TO_SUPPLIER",
        evidence_class="OFFICIAL_RECORD",
        subject_entity_id=payer,
        object_entity_id=supplier,
        value_text=str(amount),
        locator=locator,
        metadata={"payment_date": payment_date, "currency": "GBP"},
    )


def _contract(con, buyer, supplier, value="10000", start="2026-01-01", end="2026-06-30"):
    contract = _entity(con, "CONTRACT", f"contract-{buyer}-{supplier}")
    add_fact(
        con,
        document_id="doc",
        snapshot_id="snap",
        fact_type="CONTRACT_BUYER",
        predicate="BUYER_OF_CONTRACT",
        evidence_class="OFFICIAL_RECORD",
        subject_entity_id=buyer,
        object_entity_id=contract,
        locator="buyer",
    )
    add_fact(
        con,
        document_id="doc",
        snapshot_id="snap",
        fact_type="CONTRACT_SUPPLIER",
        predicate="SUPPLIER_TO_CONTRACT",
        evidence_class="OFFICIAL_RECORD",
        subject_entity_id=supplier,
        object_entity_id=contract,
        locator="supplier",
    )
    add_fact(
        con,
        document_id="doc",
        snapshot_id="snap",
        fact_type="CONTRACT_VALUE",
        predicate="HAS_VALUE",
        evidence_class="OFFICIAL_RECORD",
        subject_entity_id=contract,
        value_text=value,
        locator="value",
        metadata={"currency": "GBP"},
    )
    add_fact(
        con,
        document_id="doc",
        snapshot_id="snap",
        fact_type="CONTRACT_PERIOD",
        predicate="STARTS_ON",
        evidence_class="OFFICIAL_RECORD",
        subject_entity_id=contract,
        value_text=start,
        locator="start",
    )
    add_fact(
        con,
        document_id="doc",
        snapshot_id="snap",
        fact_type="CONTRACT_PERIOD",
        predicate="ENDS_ON",
        evidence_class="OFFICIAL_RECORD",
        subject_entity_id=contract,
        value_text=end,
        locator="end",
    )
    return contract


def test_contract_from_another_council_does_not_reconcile_payment(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    _base(con)
    council_a = _entity(con, "COUNCIL", "Council A")
    council_b = _entity(con, "COUNCIL", "Council B")
    supplier = _entity(con, "SUPPLIER", "Example Services Ltd")
    _payment(con, council_a, supplier, "12000", "2026-07-15", "payment")
    _contract(con, council_b, supplier)

    assert detect_payment_contract_value_mismatch(con) == 0
    assert con.execute("SELECT count(*) FROM signals").fetchone()[0] == 0
    con.close()


def test_credit_is_netted_and_not_treated_as_out_of_period_payment(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    _base(con)
    council = _entity(con, "COUNCIL", "Council A")
    supplier = _entity(con, "SUPPLIER", "Example Services Ltd")
    _payment(con, council, supplier, "12000", "2026-05-15", "invoice")
    _payment(con, council, supplier, "-3000", "2026-08-15", "credit")
    _contract(con, council, supplier, value="10000")

    assert detect_payment_contract_value_mismatch(con) == 0
    assert con.execute("SELECT count(*) FROM signals").fetchone()[0] == 0
    con.close()


def test_positive_out_of_period_payment_is_still_reviewed(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    _base(con)
    council = _entity(con, "COUNCIL", "Council A")
    supplier = _entity(con, "SUPPLIER", "Example Services Ltd")
    _payment(con, council, supplier, "500", "2026-08-15", "late-payment")
    _contract(con, council, supplier, value="10000")

    assert detect_payment_contract_value_mismatch(con) == 1
    signal = con.execute(
        "SELECT signal_type,object_entity_id FROM signals"
    ).fetchone()
    assert signal == ("PAYMENTS_OUTSIDE_TRACKED_CONTRACT_PERIODS", council)
    con.close()
