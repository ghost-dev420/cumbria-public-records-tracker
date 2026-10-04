from public_records_tracker.analysis import ensure_analysis_schema
from public_records_tracker.db import connect
from public_records_tracker.procurement_gaps import detect_unmatched_payment_streams
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
             'raw.csv','text/csv',200,NULL,NULL
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


def _payment(con, payer, supplier, amount, locator):
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
        metadata={"currency": "GBP"},
    )


def _contract(con, buyer, supplier):
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


def test_credits_reduce_unmatched_payment_stream_below_threshold(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    _base(con)
    council = _entity(con, "COUNCIL", "Council A")
    supplier = _entity(con, "SUPPLIER", "Example Services Ltd")
    _payment(con, council, supplier, "40000", "invoice")
    _payment(con, council, supplier, "-15000", "credit")

    assert detect_unmatched_payment_streams(con) == 0
    con.close()


def test_obvious_housekeeping_payees_are_not_procurement_gap_leads(tmp_path):
    for index, name in enumerate(("HMRC (CHAPS)", "Cumbria County Council", "99999")):
        con = connect(tmp_path / f"tracker-{index}.duckdb")
        _base(con)
        council = _entity(con, "COUNCIL", "Council A")
        supplier = _entity(con, "SUPPLIER", name)
        _payment(con, council, supplier, "50000", "payment")
        assert detect_unmatched_payment_streams(con) == 0
        con.close()


def test_contract_with_different_buyer_does_not_hide_payment_gap(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    _base(con)
    council_a = _entity(con, "COUNCIL", "Council A")
    council_b = _entity(con, "COUNCIL", "Council B")
    supplier = _entity(con, "SUPPLIER", "Example Services Ltd")
    _payment(con, council_a, supplier, "40000", "payment")
    _contract(con, council_b, supplier)

    assert detect_unmatched_payment_streams(con) == 1
    signal = con.execute(
        """SELECT object_entity_id,metadata_json
           FROM signals
           WHERE signal_type='PAYMENT_STREAM_WITHOUT_TRACKED_PROCUREMENT_LINK'"""
    ).fetchone()
    assert signal is not None
    assert signal[0] == council_a
    assert '"buyer_scoped": true' in signal[1].lower()
    con.close()


def test_same_buyer_contract_suppresses_payment_gap(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    _base(con)
    council = _entity(con, "COUNCIL", "Council A")
    supplier = _entity(con, "SUPPLIER", "Example Services Ltd")
    _payment(con, council, supplier, "40000", "payment")
    _contract(con, council, supplier)

    assert detect_unmatched_payment_streams(con) == 0
    con.close()
