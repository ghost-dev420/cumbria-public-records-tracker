from pathlib import Path

from public_records_tracker.db import connect, register_organisation
from public_records_tracker.extractors.lgsco import extract_lgsco
from public_records_tracker.models import EvidenceClass, Record
from public_records_tracker.structured import ensure_structured_schema


def test_extracts_decision_outcome_category_date_and_summary(tmp_path: Path):
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
    body = b"""<html><body><h1>Cumberland Council (25 000 276)</h1>
    <p>Category : Education &gt; Special educational needs</p>
    <p>Decision : Upheld</p><p>Decision date : 22 Jan 2026</p>
    <h2>The Ombudsman's final decision:</h2>
    <p>Summary: There was fault by the Council and it agreed a remedy.</p>
    </body></html>"""
    record = Record(
        source_id="lgsco_cumberland",
        url="https://www.lgo.org.uk/decisions/education/special-educational-needs/25-000-276",
        title="Cumberland Council (25 000 276)",
        body=body,
        content_type="text/html",
        evidence_class=EvidenceClass.REGULATORY_FINDING,
    )
    source = {
        "id": "lgsco_cumberland",
        "organisation_ids": ["cumberland_council"],
    }
    count = extract_lgsco(
        con=con,
        record=record,
        document_id="doc",
        snapshot_id="snap",
        source=source,
    )
    assert count == 5
    rows = con.execute(
        "SELECT fact_type,value_text FROM facts ORDER BY fact_type"
    ).fetchall()
    assert ("OMBUDSMAN_DECISION", "Upheld") in rows
    assert ("OMBUDSMAN_DECISION_DATE", "22 Jan 2026") in rows
    assert ("OMBUDSMAN_CATEGORY", "Education > Special educational needs") in rows
    assert any(row[0] == "OMBUDSMAN_SUMMARY" and "agreed a remedy" in row[1] for row in rows)
    con.close()
