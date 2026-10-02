from pathlib import Path

from public_records_tracker.db import connect
from public_records_tracker.extractors.modern_gov import extract_modern_gov
from public_records_tracker.models import EvidenceClass, Record
from public_records_tracker.structured import ensure_structured_schema


def _record(url: str, html: str) -> Record:
    return Record(
        source_id="modern",
        url=url,
        title="test",
        body=html.encode(),
        content_type="text/html",
        evidence_class=EvidenceClass.OFFICIAL_RECORD,
    )


def _extract(con, record: Record, document_id: str = "doc", snapshot_id: str = "snap"):
    return extract_modern_gov(
        con=con,
        record=record,
        document_id=document_id,
        snapshot_id=snapshot_id,
        source={},
    )


def test_committee_membership_and_role(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    record = _record(
        "https://cumberland.moderngov.co.uk/mgCommitteeDetails.aspx?ID=174",
        """
        <h1>Committee details</h1><h2>People Overview and Scrutiny Committee</h2>
        <ul><li><a href='/mgUserInfo.aspx?UID=160'>Councillor Dr Helen Davison</a> (Chair)</li></ul>
        """,
    )
    assert _extract(con, record) == 1
    row = con.execute(
        """select f.predicate, e.canonical_name, f.metadata_json
           from facts f join entities e on e.entity_id=f.subject_entity_id"""
    ).fetchone()
    assert row[0] == "MEMBER_OF"
    assert row[1] == "Helen Davison"
    assert '"role": "Chair"' in row[2]
    con.close()


def test_member_public_roles_without_contact_details(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    record = _record(
        "https://cumberland.moderngov.co.uk/mgUserInfo.aspx?UID=175",
        """
        <h1>Councillor Jimmy Grisdale</h1>
        <p><span>Party:</span> Labour</p><p><span>Ward:</span> Seaton</p>
        <p>Email: private-structured-test@example.invalid</p>
        <h2>Committee appointments</h2>
        <ul><li><a href='/mgCommitteeDetails.aspx?ID=170'>Regulatory Committee</a></li></ul>
        <h2>Appointments to outside bodies</h2>
        <ul><li>Cumbria Example Board</li></ul>
        """,
    )
    assert _extract(con, record) >= 4
    facts_text = "\n".join(
        value or "" for (value,) in con.execute("select value_text from facts").fetchall()
    )
    assert "private-structured-test@example.invalid" not in facts_text
    predicates = {row[0] for row in con.execute("select predicate from facts").fetchall()}
    assert {"MEMBER_OF_PARTY", "REPRESENTS_WARD", "MEMBER_OF", "APPOINTED_TO_OUTSIDE_BODY"} <= predicates
    con.close()


def test_register_redacts_land_address_from_structured_fact(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    record = _record(
        "https://cumberland.moderngov.co.uk/mgDeclarationSubmission.aspx?UID=154",
        """
        <h1>Register of interests</h1><h2>Councillor Bob Kelly</h2>
        <p>This register of interests was published on Monday 1 June 2026, 4.35 pm.</p>
        <p>1. Details of any employment, office, trade, profession or vocation carried on for profit or gain.</p>
        <table><tr><th>Member</th><th>Spouse / Partner</th></tr><tr><td>Example Employer Ltd</td><td>Other</td></tr></table>
        <p>4. Details of any beneficial interest in land which is within the area of the council.</p>
        <table><tr><th>Member</th><th>Spouse / Partner</th></tr><tr><td>1 Secret Home Street, Carlisle</td><td>None</td></tr></table>
        """,
    )
    assert _extract(con, record) == 2
    rows = con.execute(
        "select locator,value_text,metadata_json from facts order by locator"
    ).fetchall()
    assert any("Example Employer Ltd" in row[1] for row in rows)
    land = next(row for row in rows if row[0] == "register question 4")
    assert land[1] == "[REDACTED_FROM_STRUCTURED_DATA]"
    assert "Secret Home Street" not in " ".join(str(value) for row in rows for value in row)
    con.close()


def test_meeting_declaration_decision_and_vote(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    member = _record(
        "https://cumberland.moderngov.co.uk/mgUserInfo.aspx?UID=151",
        "<h1>Councillor Michael Eldon</h1><p><span>Party:</span> Labour</p>",
    )
    _extract(con, member, "member-doc", "member-snap")
    meeting = _record(
        "https://cumberland.moderngov.co.uk/ieListDocuments.aspx?CId=174&MID=14609",
        """
        <h1>Agenda and minutes</h1>
        <h2>People Overview and Scrutiny Committee - Friday 26 June 2026 10.30 am</h2>
        <p><span>Venue:</span> Council Chamber</p>
        <table>
          <tr><td>PEOS.20/26</td><td>Disclosures of Interest Minutes: Councillor Eldon declared a registrable interest in respect of agenda item 8.</td></tr>
          <tr><td>PEOS.21/26</td><td>Example Decision Minutes: RESOLVED that the proposal be approved. The vote was For: 5 Against: 2.</td></tr>
        </table>
        """,
    )
    _extract(con, meeting, "meeting-doc", "meeting-snap")
    predicates = {row[0] for row in con.execute("select predicate from facts").fetchall()}
    assert "DECLARED_INTEREST_AT" in predicates
    assert "DECISION_TEXT" in predicates
    assert "VOTE_TEXT" in predicates
    assert "VENUE" in predicates
    con.close()
