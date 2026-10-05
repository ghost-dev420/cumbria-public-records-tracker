from pathlib import Path

from public_records_tracker.config import load_sources
from public_records_tracker.db import connect, register_source
from public_records_tracker.extractors.payments import _payer_organisation_id
from public_records_tracker.models import EvidenceClass, Record
from public_records_tracker.publication import publication_status_rows


def _record(title: str, url: str) -> Record:
    return Record(
        source_id="cumberland_transparency",
        url=url,
        title=title,
        body=b"",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        evidence_class=EvidenceClass.OFFICIAL_RECORD,
    )


def test_cumberland_transparency_gets_payment_series_defaults():
    sources = load_sources(Path("config/sources.yml"))
    source = next(item for item in sources if item["id"] == "cumberland_transparency")
    assert "payments" in source["extractors"]
    assert source["payment_payer_organisation_id"] == "cumberland_council"
    assert any("field_document_target_id=1589" in url for url in source["start_urls"])
    assert any("field_document_target_id=1433" in url for url in source["start_urls"])


def test_cumberland_legacy_spending_files_keep_predecessor_payer():
    sources = load_sources(Path("config/sources.yml"))
    source = next(item for item in sources if item["id"] == "cumberland_transparency")

    cases = [
        (
            "Allerdale spending September 2022",
            "https://www.cumberland.gov.uk/sites/default/files/2025-03/allerdale_spending_september_2022.xlsx",
            "allerdale_borough_council",
        ),
        (
            "Carlisle spending January 2023",
            "https://www.cumberland.gov.uk/sites/default/files/2025-03/carlisle_spending_january_2023.xlsx",
            "carlisle_city_council",
        ),
        (
            "Copeland expenditure December 2022",
            "https://www.cumberland.gov.uk/sites/default/files/2025-03/copeland_expenditure_december_2022.xlsx",
            "copeland_borough_council",
        ),
        (
            "Cumbria County Council spending February 2023",
            "https://www.cumberland.gov.uk/sites/default/files/2025-03/cumbria_county_council_spending_february_2023.xlsx",
            "cumbria_county_council",
        ),
    ]
    for title, url, expected in cases:
        assert _payer_organisation_id(source, _record(title, url)) == expected


def test_current_cumberland_spending_still_defaults_to_current_council():
    sources = load_sources(Path("config/sources.yml"))
    source = next(item for item in sources if item["id"] == "cumberland_transparency")
    record = _record(
        "Cumberland Council spending July 2026",
        "https://www.cumberland.gov.uk/sites/default/files/2026-08/cumberland_spending_july_2026.xlsx",
    )
    assert _payer_organisation_id(source, record) == "cumberland_council"


def test_publication_status_uses_configured_expectation(tmp_path):
    sources = load_sources(Path("config/sources.yml"))
    source = next(item for item in sources if item["id"] == "cumberland_transparency")
    con = connect(tmp_path / "tracker.duckdb")
    register_source(con, source)
    rows = publication_status_rows(con)
    row = next(item for item in rows if item["source_id"] == "cumberland_transparency")
    assert row["dataset"] == "Spending over £250"
    assert row["status"] == "not_checked"
    con.close()
