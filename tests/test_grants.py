from pathlib import Path

from public_records_tracker.db import connect, register_organisation
from public_records_tracker.extractors.grants import extract_grant_awards
from public_records_tracker.models import EvidenceClass, Record
from public_records_tracker.structured import ensure_structured_schema


def test_extracts_named_grant_recipient_amount_and_purpose(tmp_path: Path):
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
    record = Record(
        source_id="grants",
        url="https://example.gov/grants",
        title="Community grants",
        body=b"""<html><body><h3>Successful grants from round one in 2026 to 2027</h3>
        <ul><li>Example Community Hall - replacement roof: Â        <ul><li>Example Community Hall - replacement roof: \xc2£        <ul><li>Example Community Hall - replacement roof: \xc2\xa31,500</li>
        <li>Second Group - equipment: Â        <li>Second Group - equipment: \xc2£        <li>Second Group - equipment: \xc2\xa3600</li></ul></body></html>""",
        content_type="text/html",
        evidence_class=EvidenceClass.OFFICIAL_RECORD,
    )
    source = {
        "id": "westmorland_furness_south_lakeland_grants",
        "name": "Official grants",
        "organisation_ids": ["westmorland_furness_council"],
    }
    count = extract_grant_awards(
        con=con,
        record=record,
        document_id="doc",
        snapshot_id="snap",
        source=source,
    )
    assert count == 4
    rows = con.execute(
        """SELECT f.fact_type,f.value_text,e.canonical_name,f.metadata_json
           FROM facts f
           LEFT JOIN entities e ON e.entity_id=f.subject_entity_id
           ORDER BY f.fact_type,e.canonical_name"""
    ).fetchall()
    assert any(row[0] == "GRANT_RECIPIENT" and row[1] == "1500" and row[2] == "Example Community Hall" for row in rows)
    assert any(row[0] == "GRANT_AWARD" and row[1] == "600" for row in rows)
    assert any("replacement roof" in row[3] for row in rows)
    con.close()


def test_ignores_generic_funding_list_without_successful_grants_heading(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    record = Record(
        source_id="grants",
        url="https://example.gov/apply",
        title="Apply for funding",
        body=b"<html><body><h3>Available grants</h3><ul><li>Example - apply for up to Â<html><body><h3>Available grants</h3><ul><li>Example - apply for up to \xc2£<html><body><h3>Available grants</h3><ul><li>Example - apply for up to \xc2\xa31,000</li></ul></body></html>",
        content_type="text/html",
        evidence_class=EvidenceClass.OFFICIAL_RECORD,
    )
    count = extract_grant_awards(
        con=con,
        record=record,
        document_id="doc",
        snapshot_id="snap",
        source={"id": "grants", "organisation_ids": []},
    )
    assert count == 0
    con.close()
