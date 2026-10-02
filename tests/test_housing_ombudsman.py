from pathlib import Path

from public_records_tracker.db import connect
from public_records_tracker.extractors.housing_ombudsman import extract_housing_ombudsman
from public_records_tracker.models import EvidenceClass, Record
from public_records_tracker.structured import ensure_structured_schema


def test_extracts_current_housing_ombudsman_decision(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    html = """<html><body><h1>Home Group Limited (202427524)</h1>
    <table><tr><th>Case ID</th><td>202427524</td></tr>
    <tr><th>Decision type</th><td>Investigation</td></tr>
    <tr><th>Landlord</th><td>Home Group Limited</td></tr>
    <tr><th>Date</th><td>24 April 2026</td></tr></table>
    <h2>Our decision (determination)</h2>
    <p>We found reasonable redress on one complaint.</p>
    <p>There was no maladministration in complaint handling.</p>
    <h2>Summary of reasons</h2></body></html>"""
    record = Record(
        source_id="housing_ombudsman_home_group",
        url="https://www.housing-ombudsman.org.uk/decisions/home-group-limited-202427524/",
        title="Home Group Limited (202427524)",
        body=html.encode(),
        content_type="text/html",
        evidence_class=EvidenceClass.REGULATORY_FINDING,
    )
    count = extract_housing_ombudsman(
        con=con,
        record=record,
        document_id="doc",
        snapshot_id="snap",
        source={"id": "housing_ombudsman_home_group"},
    )
    assert count == 4
    rows = con.execute("SELECT fact_type,value_text FROM facts ORDER BY fact_type").fetchall()
    assert ("HOUSING_OMBUDSMAN_CASE", "202427524") in rows
    assert ("HOUSING_OMBUDSMAN_DECISION_TYPE", "Investigation") in rows
    assert ("HOUSING_OMBUDSMAN_DECISION_DATE", "24 April 2026") in rows
    assert any(
        row[0] == "HOUSING_OMBUDSMAN_DETERMINATION"
        and "no maladministration" in row[1].casefold()
        for row in rows
    )
    con.close()


def test_ignores_non_decision_page(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    record = Record(
        source_id="housing_ombudsman_home_group",
        url="https://www.housing-ombudsman.org.uk/decisions/",
        title="Decisions",
        body=b"<html><body>archive</body></html>",
        content_type="text/html",
        evidence_class=EvidenceClass.REGULATORY_FINDING,
    )
    assert extract_housing_ombudsman(
        con=con,
        record=record,
        document_id="doc",
        snapshot_id="snap",
        source={"id": "housing_ombudsman_home_group"},
    ) == 0
    con.close()
