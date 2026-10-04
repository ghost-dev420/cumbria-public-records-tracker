from pathlib import Path

from public_records_tracker.config import load_sources


def test_base_production_registry_contains_cumberland_contract_register() -> None:
    by_id = {
        source["id"]: source
        for source in load_sources(Path("config/sources.yml"))
    }
    source = by_id["cumberland_contract_register"]

    assert source["kind"] == "html_listing"
    assert source["evidence_class"] == "OFFICIAL_RECORD"
    assert source["organisation_ids"] == ["cumberland_council"]
    assert source["contract_buyer_organisation_id"] == "cumberland_council"
    assert source["extractors"] == ["contract_register_csv"]
    assert source["expect_facts"] is True
    assert source["contract_supplier_aliases"]["AWSL"] == "Allerdale Waste Services Limited"
    assert source["contract_supplier_aliases"]["GLL"] == "Greenwich Leisure Ltd"
