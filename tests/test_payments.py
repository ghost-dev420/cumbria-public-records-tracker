import json

from public_records_tracker.db import connect, register_organisation
from public_records_tracker.extractors.payments import extract_payments
from public_records_tracker.models import EvidenceClass, Record
from public_records_tracker.structured import ensure_structured_schema


SOURCE = {
    "id": "westmorland_furness_spending",
    "name": "Westmorland and Furness spending over 250 pounds",
    "organisation_ids": ["westmorland_furness_council"],
    "payment_payer_organisation_id": "westmorland_furness_council",
}


def _record(title: str, body: str) -> Record:
    return Record(
        source_id=SOURCE["id"],
        url="https://example.test/spending.csv",
        title=title,
        body=body.encode("utf-8"),
        content_type="text/csv",
        evidence_class=EvidenceClass.OFFICIAL_RECORD,
        metadata={"anchor_text": title},
    )


def _db(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    register_organisation(
        con,
        {
            "id": "westmorland_furness_council",
            "name": "Westmorland and Furness Council",
            "organisation_type": "unitary_authority",
            "status": "current",
        },
    )
    return con


def test_extracts_trade_supplier_payment_with_row_locator(tmp_path):
    con = _db(tmp_path)
    record = _record(
        "August 2026 transparency spending over £250 trade suppliers",
        "Payment Date,Supplier Name,Amount,Description,Directorate,Transaction Number\n"
        "14/08/2026,Example Engineering Ltd,1,234.50,Bridge inspection,Highways,TX-42\n",
    )
    # Quote the comma-containing amount as a real council CSV would.
    record.body = record.body.replace(b"1,234.50", b'"1,234.50"')

    added = extract_payments(
        con=con,
        record=record,
        document_id="doc-1",
        snapshot_id="snap-1",
        source=SOURCE,
    )

    assert added == 1
    row = con.execute(
        """SELECT f.value_text,f.locator,f.metadata_json,
                  payer.canonical_name,supplier.canonical_name
           FROM facts f
           LEFT JOIN entities payer ON payer.entity_id=f.subject_entity_id
           LEFT JOIN entities supplier ON supplier.entity_id=f.object_entity_id
           WHERE f.predicate='PAYMENT_TO_SUPPLIER'"""
    ).fetchone()
    assert row[0] == "1234.50"
    assert row[1] == "CSV row 2"
    assert row[3] == "Westmorland and Furness Council"
    assert row[4] == "Example Engineering Ltd"
    metadata = json.loads(row[2])
    assert metadata["payment_date"] == "2026-08-14"
    assert metadata["description"] == "Bridge inspection"
    assert metadata["department"] == "Highways"
    assert metadata["reference"] == "TX-42"
    con.close()


def test_private_home_and_support_files_are_not_structured(tmp_path):
    con = _db(tmp_path)
    body = "Payment Date,Supplier Name,Amount\n14/08/2026,Private Recipient,500.00\n"
    for title in (
        "August 2026 transparency spending over £250 private homes",
        "August 2026 transparency spending over £250 support related payments",
    ):
        added = extract_payments(
            con=con,
            record=_record(title, body),
            document_id=f"doc-{title}",
            snapshot_id=f"snap-{title}",
            source=SOURCE,
        )
        assert added == 0

    assert con.execute("SELECT count(*) FROM facts").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM entities WHERE entity_type='SUPPLIER'").fetchone()[0] == 0
    con.close()
