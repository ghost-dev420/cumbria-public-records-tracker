from public_records_tracker.db import connect, register_organisation
from public_records_tracker.extractors.contract_register_csv import extract_contract_register_csv
from public_records_tracker.models import EvidenceClass, Record
from public_records_tracker.resolution import ensure_resolution_schema
from public_records_tracker.structured import ensure_structured_schema


def _source():
    return {
        "id": "cumberland_contract_register",
        "name": "Cumberland Council official contracts register",
        "organisation_ids": ["cumberland_council"],
        "contract_buyer_organisation_id": "cumberland_council",
    }


def _setup(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    register_organisation(
        con,
        {
            "id": "cumberland_council",
            "name": "Cumberland Council",
            "organisation_type": "unitary_authority",
            "status": "current",
            "jurisdiction": "Cumberland",
        },
    )
    return con


def test_extracts_official_contract_register_relationships_period_and_value(tmp_path):
    con = _setup(tmp_path)
    body = (
        "Contract Reference,Contract Name,Description,Supplier,Company Number,"
        "Procurement Method,Start Date,End Date,Contract Review Date,"
        "Contract Value (ex VAT),Contract Value (incl VAT)\n"
        'CC-123,Children Individual Access Needs Programme,Design services,Day Cummins Ltd,'
        '01234567,Framework Call Off,02/06/2025,02/06/2027,01/12/2026,59800,71760\n'
    ).encode()
    record = Record(
        source_id="cumberland_contract_register",
        url="https://www.cumberland.gov.uk/example/contracts.csv",
        title="Contracts Register",
        body=body,
        content_type="text/csv",
        evidence_class=EvidenceClass.OFFICIAL_RECORD,
    )

    count = extract_contract_register_csv(
        con=con,
        record=record,
        document_id="doc",
        snapshot_id="snap",
        source=_source(),
    )
    assert count >= 8

    facts = con.execute(
        "SELECT predicate,value_text FROM facts ORDER BY predicate,value_text"
    ).fetchall()
    predicates = [row[0] for row in facts]
    assert "BUYER_OF_CONTRACT" in predicates
    assert "SUPPLIER_TO_CONTRACT" in predicates
    assert ("STARTS_ON", "2025-06-02") in facts
    assert ("ENDS_ON", "2027-06-02") in facts
    assert ("HAS_VALUE", "59800.00") in facts
    assert ("REVIEW_ON", "2026-12-01") in facts
    assert ("HAS_PROCUREMENT_METHOD", "Framework Call Off") in facts

    supplier = con.execute(
        "SELECT entity_id FROM entities WHERE canonical_name='Day Cummins Ltd'"
    ).fetchone()[0]
    assert con.execute(
        "SELECT scheme,identifier FROM entity_identifiers WHERE entity_id=?",
        [supplier],
    ).fetchone() == ("gb-coh", "01234567")
    con.close()


def test_ict_appendix_annual_cost_is_not_treated_as_contract_ceiling(tmp_path):
    con = _setup(tmp_path)
    body = (
        "Number,Owning Authority,Short description,Vendor,Starts,Ends,Options,"
        "Renewal/Extension end date,Initial cost,Annual cost (ex VAT)\n"
        'ICT-42,Cumberland Council,Microsoft M365 ESA,"Phoenix Software Ltd; Softcat PLC",'
        '01/09/2025,31/03/2028,1 year,31/03/2029,25000,120000\n'
    ).encode()
    record = Record(
        source_id="cumberland_contract_register",
        url="https://www.cumberland.gov.uk/example/contracts_appendix.csv",
        title="Contracts Register ICT appendix",
        body=body,
        content_type="text/csv",
        evidence_class=EvidenceClass.OFFICIAL_RECORD,
    )

    extract_contract_register_csv(
        con=con,
        record=record,
        document_id="doc-ict",
        snapshot_id="snap-ict",
        source=_source(),
    )

    supplier_names = {
        row[0]
        for row in con.execute(
            """SELECT e.canonical_name
               FROM facts f
               JOIN entities e ON e.entity_id=f.subject_entity_id
               WHERE f.predicate='SUPPLIER_TO_CONTRACT'"""
        ).fetchall()
    }
    assert supplier_names == {"Phoenix Software Ltd", "Softcat PLC"}
    assert con.execute(
        "SELECT count(*) FROM facts WHERE predicate='HAS_VALUE'"
    ).fetchone()[0] == 0
    assert con.execute(
        "SELECT count(*) FROM facts WHERE predicate='HAS_VALUE_INCLUDING_VAT'"
    ).fetchone()[0] == 0
    facts = con.execute(
        "SELECT predicate,value_text FROM facts"
    ).fetchall()
    assert ("STARTS_ON", "2025-09-01") in facts
    assert ("ENDS_ON", "2028-03-31") in facts
    con.close()
