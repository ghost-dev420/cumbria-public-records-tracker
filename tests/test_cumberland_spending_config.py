from pathlib import Path

from public_records_tracker.config import load_sources
from public_records_tracker.db import connect, register_source
from public_records_tracker.publication import publication_status_rows


def test_cumberland_transparency_gets_payment_series_defaults():
    sources = load_sources(Path("config/sources.yml"))
    source = next(item for item in sources if item["id"] == "cumberland_transparency")
    assert "payments" in source["extractors"]
    assert source["payment_payer_organisation_id"] == "cumberland_council"
    assert any("field_document_target_id=1589" in url for url in source["start_urls"])
    assert any("field_document_target_id=1433" in url for url in source["start_urls"])


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
