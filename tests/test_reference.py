import csv
import io
import json
import zipfile
from pathlib import Path

from public_records_tracker.db import connect
from public_records_tracker.reference import (
    ensure_reference_schema,
    import_charity_register,
    import_companies_house_basic,
    import_companies_house_psc,
)
from public_records_tracker.resolution import add_alias, ensure_resolution_schema, run_resolution
from public_records_tracker.structured import ensure_structured_schema, upsert_entity


def _zip_csv(path: Path, filename: str, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(filename, buffer.getvalue())


def _zip_tsv(path: Path, filename: str, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, delimiter="\t")
    writer.writeheader()
    writer.writerows(rows)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(filename, buffer.getvalue())


def test_company_bulk_adds_identifier_and_resolves(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    ensure_reference_schema(con)
    supplier = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="Acme Services Ltd",
        namespace="contracts",
    )
    add_alias(
        con,
        entity_id=supplier,
        alias_text="Acme Services Ltd",
        source="fixture",
    )

    source = tmp_path / "companies.zip"
    _zip_csv(
        source,
        "BasicCompanyData.csv",
        ["CompanyName", "CompanyNumber", "CompanyStatus", "CompanyCategory"],
        [
            {
                "CompanyName": "ACME SERVICES LIMITED",
                "CompanyNumber": "01234567",
                "CompanyStatus": "Active",
                "CompanyCategory": "Private Limited Company",
            }
        ],
    )
    stats = import_companies_house_basic(
        con,
        source,
        source_url="https://example.test/BasicCompanyDataAsOneFile-2026-10-01.zip",
    )
    assert stats["matched_companies"] == 1
    company = con.execute(
        """SELECT entity_id FROM entity_identifiers
           WHERE scheme='gb-coh' AND identifier='01234567'"""
    ).fetchone()[0]
    run_resolution(con)
    canonical_count = con.execute(
        """SELECT count(DISTINCT canonical_entity_id)
           FROM resolved_entity_members
           WHERE entity_id IN (?,?)""",
        [supplier, company],
    ).fetchone()[0]
    assert canonical_count == 1
    con.close()


def test_charity_bulk_adds_charity_and_company_identifiers(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    ensure_reference_schema(con)
    body = upsert_entity(
        con,
        entity_type="ORGANISATION",
        name="Example Community Trust",
        namespace="modern_gov",
    )
    add_alias(
        con,
        entity_id=body,
        alias_text="Example Community Trust",
        source="fixture",
    )
    source = tmp_path / "charity.zip"
    _zip_tsv(
        source,
        "charity.txt",
        ["regno", "name", "company_number"],
        [
            {
                "regno": "1234567",
                "name": "Example Community Trust",
                "company_number": "07654321",
            }
        ],
    )
    stats = import_charity_register(
        con,
        source,
        source_url="https://example.test/charity.zip",
    )
    assert stats["matched_charities"] == 1
    identifiers = con.execute(
        """SELECT scheme,identifier FROM entity_identifiers
           WHERE entity_id IN (
             SELECT entity_id FROM registry_records
             WHERE registry='charity_commission' AND external_id='1234567'
           )
           ORDER BY scheme"""
    ).fetchall()
    assert ("gb-chc", "1234567") in identifiers
    assert ("gb-coh", "07654321") in identifiers
    con.close()


def test_psc_import_stores_relationship_without_private_address(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    ensure_reference_schema(con)
    company = upsert_entity(
        con,
        entity_type="COMPANY",
        name="Acme Services Limited",
        namespace="companies_house",
    )
    con.execute(
        """INSERT INTO registry_records(
             registry_record_id,registry,record_type,external_id,entity_id,
             canonical_name,source_url,dataset_date,metadata_json
           ) VALUES ('r1','companies_house','company','01234567',?,
                     'Acme Services Limited','fixture','2026-10-01','{}')""",
        [company],
    )
    payload = {
        "company_number": "01234567",
        "kind": "individual-person-with-significant-control",
        "data": {
            "name": "Jane Example",
            "natures_of_control": ["ownership-of-shares-25-to-50-percent"],
            "address": {"address_line_1": "Private address must not be copied"},
        },
    }
    source = tmp_path / "psc.zip"
    with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("psc.json", json.dumps(payload) + "\n")
    stats = import_companies_house_psc(
        con,
        source,
        source_url="https://example.test/psc-snapshot-2026-10-01.zip",
    )
    assert stats["matched_psc"] == 1
    row = con.execute(
        """SELECT metadata_json FROM registry_relationships
           WHERE predicate='PSC_OF_COMPANY'"""
    ).fetchone()
    assert "Private address" not in row[0]
    assert "ownership-of-shares" in row[0]
    con.close()
