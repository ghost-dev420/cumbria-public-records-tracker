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


def test_extracts_multiple_cases_directly_from_search_result_cards(tmp_path: Path):
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
    body = b"""<html><body>
    <div>Westmorland and Furness Council (26 002 445)</div>
    <div>Statement Closed after initial enquiries Parking and other penalties 09-Aug-2026</div>
    <p>Summary: We will not investigate Mr X's complaint about parking restrictions.</p>
    <div>Westmorland and Furness Council (25 014 511)</div>
    <div>Statement Upheld Refuse and recycling 28-Apr-2026</div>
    <p>Summary: Miss X complained the Council repeatedly missed assisted collections.</p>
    </body></html>"""
    record = Record(
        source_id="lgsco_westmorland_furness",
        url="https://www.lgo.org.uk/Decisions/SearchResults?page=1",
        title="LGSCO search results",
        body=body,
        content_type="text/html",
        evidence_class=EvidenceClass.REGULATORY_FINDING,
        metadata={"listing": True, "lgsco_search": True, "parse_listing_results": True},
    )
    source = {
        "id": "lgsco_westmorland_furness",
        "organisation_ids": ["westmorland_furness_council"],
    }
    count = extract_lgsco(
        con=con,
        record=record,
        document_id="search-doc",
        snapshot_id="search-snap",
        source=source,
    )
    assert count == 10
    rows = con.execute(
        "SELECT fact_type,value_text FROM facts ORDER BY value_text"
    ).fetchall()
    assert ("OMBUDSMAN_CASE", "26-002-445") in rows
    assert ("OMBUDSMAN_DECISION", "Closed after initial enquiries") in rows
    assert ("OMBUDSMAN_CATEGORY", "Parking and other penalties") in rows
    assert ("OMBUDSMAN_DECISION_DATE", "09-Aug-2026") in rows
    assert ("OMBUDSMAN_CASE", "25-014-511") in rows
    assert ("OMBUDSMAN_DECISION", "Upheld") in rows
    assert any(row[0] == "OMBUDSMAN_SUMMARY" and "assisted collections" in row[1] for row in rows)
    con.close()
