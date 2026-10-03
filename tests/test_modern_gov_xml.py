from pathlib import Path

from public_records_tracker.db import connect
from public_records_tracker.extractors.modern_gov_xml import extract_modern_gov_xml
from public_records_tracker.models import EvidenceClass, Record
from public_records_tracker.structured import ensure_structured_schema


def _record(operation: str, xml: str) -> Record:
    return Record(
        source_id="modern-xml",
        url=f"https://example.moderngov.co.uk/mgWebService.asmx/{operation}",
        title=operation,
        body=xml.encode(),
        content_type="text/xml",
        evidence_class=EvidenceClass.OFFICIAL_RECORD,
        metadata={"operation": operation},
    )


def _extract(con, record: Record) -> int:
    return extract_modern_gov_xml(
        con=con,
        record=record,
        document_id="doc",
        snapshot_id="snap",
        source={},
    )


def test_extracts_committees(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    record = _record(
        "GetCommittees",
        """<?xml version='1.0'?>
        <committees>
          <committeescount>1</committeescount>
          <committee>
            <committeeid>272</committeeid>
            <committeetitle>Audit Committee</committeetitle>
            <committeedeleted>False</committeedeleted>
            <committeeexpired>False</committeeexpired>
            <committeecategory>Regulatory</committeecategory>
          </committee>
        </committees>""",
    )
    assert _extract(con, record) == 1
    row = con.execute(
        """select f.predicate,e.canonical_name,f.metadata_json
           from facts f join entities e on e.entity_id=f.subject_entity_id"""
    ).fetchone()
    assert row[0] == "COMMITTEE_LISTED"
    assert row[1] == "Audit Committee"
    assert '"modern_gov_committee_id": "272"' in row[2]
    con.close()


def test_extracts_councillor_public_facts_but_not_contact_details(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    record = _record(
        "GetCouncillorsByWard",
        """<?xml version='1.0'?>
        <councillorsbyward>
          <wards>
            <ward>
              <wardtitle>Example Ward</wardtitle>
              <councillors>
                <councillor>
                  <councillorid>7377</councillorid>
                  <fullusername>Councillor Jane Example</fullusername>
                  <politicalpartytitle>Example Party</politicalpartytitle>
                  <keyposts>Committee Chair</keyposts>
                  <workaddress><email>jane@example.invalid</email></workaddress>
                  <homeaddress>
                    <line1>1 Private Home Street</line1>
                    <phone>01234 567890</phone>
                  </homeaddress>
                  <termsofoffice>
                    <termofoffice>
                      <startdate>01/05/2025</startdate>
                      <enddate>unspecified</enddate>
                    </termofoffice>
                  </termsofoffice>
                </councillor>
              </councillors>
            </ward>
          </wards>
        </councillorsbyward>""",
    )
    assert _extract(con, record) == 4
    predicates = {row[0] for row in con.execute("select predicate from facts").fetchall()}
    assert {"REPRESENTS_WARD", "MEMBER_OF_PARTY", "PUBLIC_ROLE_TEXT", "TERM_OF_OFFICE"} <= predicates
    fact_text = "\n".join(
        str(value or "")
        for row in con.execute("select value_text,metadata_json from facts").fetchall()
        for value in row
    )
    assert "Private Home Street" not in fact_text
    assert "01234 567890" not in fact_text
    assert "jane@example.invalid" not in fact_text
    con.close()


def test_extracts_meeting_without_needing_html(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    record = _record(
        "GetCalendarEvents",
        """<?xml version='1.0'?>
        <events>
          <event>
            <meetingid>14609</meetingid>
            <meetingtitle>Audit Committee - 3 October 2026</meetingtitle>
            <meetingdate>03/10/2026 10:30</meetingdate>
            <committeeid>272</committeeid>
            <committeetitle>Audit Committee</committeetitle>
            <venue>Council Chamber</venue>
          </event>
        </events>""",
    )
    assert _extract(con, record) == 3
    predicates = {row[0] for row in con.execute("select predicate from facts").fetchall()}
    assert {"MEETING_OF", "MEETING_DATE", "VENUE"} <= predicates
    con.close()
