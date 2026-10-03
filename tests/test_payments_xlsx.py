from io import BytesIO

from openpyxl import Workbook

from public_records_tracker.db import connect, register_organisation
from public_records_tracker.extractors.payments_xlsx import extract_payments_xlsx
from public_records_tracker.models import EvidenceClass, Record
from public_records_tracker.structured import ensure_structured_schema


def _workbook_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "August"
    sheet.append(["Cumberland Council expenditure over £250"])
    sheet.append(["Payment Date", "Supplier Name", "Amount", "Description", "Directorate"])
    sheet.append(["14/08/2026", "Example Engineering Ltd", 1234.50, "Bridge inspection", "Highways"])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_extracts_payment_from_xlsx_with_heading_row(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    register_organisation(
        con,
        {
            "id": "cumberland_council",
            "name": "Cumberland Council",
            "organisation_type": "unitary_authority",
            "status": "current",
        },
    )
    source = {
        "id": "cumberland_transparency",
        "name": "Cumberland Council transparency documents",
        "organisation_ids": ["cumberland_council"],
        "payment_payer_organisation_id": "cumberland_council",
        "allow_payment_csv_by_header": True,
    }
    record = Record(
        source_id=source["id"],
        url="https://example.test/August-2026.xlsx",
        title="August 2026 council expenditure over £250",
        body=_workbook_bytes(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        evidence_class=EvidenceClass.OFFICIAL_RECORD,
        metadata={"anchor_text": "August 2026 council expenditure over £250"},
    )

    added = extract_payments_xlsx(
        con=con,
        record=record,
        document_id="doc-xlsx",
        snapshot_id="snap-xlsx",
        source=source,
    )

    assert added == 1
    row = con.execute(
        """SELECT f.value_text,f.locator,payer.canonical_name,supplier.canonical_name
           FROM facts f
           LEFT JOIN entities payer ON payer.entity_id=f.subject_entity_id
           LEFT JOIN entities supplier ON supplier.entity_id=f.object_entity_id
           WHERE f.predicate='PAYMENT_TO_SUPPLIER'"""
    ).fetchone()
    assert row == (
        "1234.50",
        "XLSX August!row 3",
        "Cumberland Council",
        "Example Engineering Ltd",
    )
    con.close()
