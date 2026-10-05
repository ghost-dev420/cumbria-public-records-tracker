from pathlib import Path

from public_records_tracker.config import load_sources


def _assert_cumberland_contract_register(path: str) -> None:
    by_id = {
        source["id"]: source
        for source in load_sources(Path(path))
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
    assert source["contract_supplier_aliases"]["Tivoli"] == "TIVOLI GROUP LTD"


def test_base_production_registry_contains_cumberland_contract_register() -> None:
    _assert_cumberland_contract_register("config/sources.yml")


def test_android_registry_inherits_contract_supplier_alias_defaults() -> None:
    _assert_cumberland_contract_register("config/sources-android-full.yml")
