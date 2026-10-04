import json

from public_records_tracker.db import connect, register_organisation
from public_records_tracker.extractors.payments import extract_payments
from public_records_tracker.models import EvidenceClass, Record
from public_records_tracker.structured import add_fact, ensure_structured_schema, upsert_entity


def test_reextract_replaces_dirty_channel_supplier_fact(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    register_organisation(
        con,
        {
            "id": "council_a",
            "name": "Council A",
            "organisation_type": "unitary_authority",
            "status": "current",
        },
    )
    old_supplier = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="NPOWER LTD (NCR)",
        namespace="payments:test-payments",
    )
    add_fact(
        con,
        document_id="doc",
        snapshot_id="snap",
        fact_type="PAYMENT",
        predicate="PAYMENT_TO_SUPPLIER",
        evidence_class="OFFICIAL_RECORD",
        object_entity_id=old_supplier,
        value_text="1234.56",
        locator="CSV row 2",
    )

    record = Record(
        source_id="test-payments",
        url="https://example.test/supplier-payments.csv",
        title="Supplier payments.csv",
        body=b"Supplier,Amount,Date\nNPOWER LTD (NCR),1234.56,01/09/2026\n",
        content_type="text/csv",
        evidence_class=EvidenceClass.OFFICIAL_RECORD,
    )
    source = {
        "id": "test-payments",
        "name": "Council A supplier payments",
        "organisation_ids": ["council_a"],
        "payment_payer_organisation_id": "council_a",
    }

    assert extract_payments(
        con=con,
        record=record,
        document_id="doc",
        snapshot_id="snap",
        source=source,
    ) == 1

    rows = con.execute(
        """SELECT e.canonical_name,f.metadata_json
           FROM facts f
           JOIN entities e ON e.entity_id=f.object_entity_id
           WHERE f.document_id='doc' AND f.snapshot_id='snap'
             AND f.predicate='PAYMENT_TO_SUPPLIER'"""
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "NPOWER LTD"
    metadata = json.loads(rows[0][1])
    assert metadata["raw_supplier_name"] == "NPOWER LTD (NCR)"
    assert metadata["payment_channel"] == "NCR"
    con.close()
