from pathlib import Path

from public_records_tracker.config import load_sources


def test_android_full_registry_inherits_production_and_replaces_blocked_moderngov() -> None:
    sources = load_sources(Path("config/sources-android-full.yml"))
    by_id = {source["id"]: source for source in sources}

    for source_id in (
        "cumberland_transparency",
        "cumberland_contract_register",
        "westmorland_furness_spending",
        "workington_town_meetings",
        "workington_town_transparency",
        "contracts_finder",
        "lgsco_cumberland",
        "lgsco_westmorland_furness",
        "housing_ombudsman_home_group",
        "find_a_tender",
        "westmorland_furness_south_lakeland_grants",
    ):
        assert source_id in by_id

    for source_id in (
        "cumberland_meetings",
        "cumberland_moderngov_structure",
        "cumberland_parish_directory",
        "westmorland_furness_meetings",
        "westmorland_furness_moderngov_structure",
        "westmorland_furness_parish_directory",
        "carlisle_current_and_legacy",
    ):
        assert source_id not in by_id

    for source_id in ("cumberland_moderngov_api", "westmorland_furness_moderngov_api"):
        source = by_id[source_id]
        assert source["kind"] == "modern_gov_xml"
        assert "GetCalendarEvents" in source["api_operations"]
        assert source["expand_meetings"] is True
        assert source["page_limit"] >= 300


def test_android_full_registry_removes_known_blocked_or_stale_start_urls() -> None:
    sources = load_sources(Path("config/sources-android-full.yml"))
    by_id = {source["id"]: source for source in sources}

    selected = (
        "cumberland_legacy_authorities",
        "westmorland_legacy_authorities",
        "wigton_town_records",
        "whitehaven_town_records",
    )
    urls = "\n".join(
        str(url)
        for source_id in selected
        for url in by_id[source_id].get("start_urls", [])
    )
    assert "moderngov.co.uk" not in urls
    assert "wigtontown.com" not in urls
    assert "wigton-tc.gov.uk" in urls
    assert "whitehaventowncouncil.co.uk" in urls


def test_android_cumberland_contract_register_is_structured_official_source() -> None:
    sources = load_sources(Path("config/sources-android-full.yml"))
    by_id = {source["id"]: source for source in sources}
    source = by_id["cumberland_contract_register"]

    assert source["kind"] == "html_listing"
    assert source["evidence_class"] == "OFFICIAL_RECORD"
    assert source["organisation_ids"] == ["cumberland_council"]
    assert source["contract_buyer_organisation_id"] == "cumberland_council"
    assert source["extractors"] == ["contract_register_csv"]
    assert source["expect_facts"] is True
    assert source["minimum_fact_count"] == 1
    assert len(source["start_urls"]) == 1
    assert source["start_urls"][0].endswith("/contract-register")
    assert "contracts_register" in source["include_link_regex"]


def test_android_lgsco_uses_dedicated_collectors() -> None:
    sources = load_sources(Path("config/sources-android-full.yml"))
    by_id = {source["id"]: source for source in sources}

    expected = {
        "lgsco_cumberland": ("Cumberland Council", "lgsco_search"),
        "lgsco_westmorland_furness": (
            "Westmorland and Furness Council",
            "lgsco_browser",
        ),
    }
    for source_id, (organisation_name, kind) in expected.items():
        source = by_id[source_id]
        assert source["kind"] == kind
        assert source["organisation_name"] == organisation_name
        assert source["endpoint"].endswith("/Decisions/SearchResults")
        assert source["prime_url"].endswith("/decisions")
        assert source["from_date"] == "0001-01-01"
        assert source["decision_codes"] == "c+nu+u+"
        assert source["sort_order"] == "descending"
        assert source["page_limit"] >= 20
        assert source["ignore_decision_statuses"] == [404, 410]
        assert source["extractors"] == ["lgsco"]
        assert source["expect_facts"] is True
        assert source["minimum_fact_count"] == 1


def test_westmorland_lgsco_has_official_performance_fallbacks() -> None:
    sources = load_sources(Path("config/sources-android-full.yml"))
    by_id = {source["id"]: source for source in sources}
    source = by_id["lgsco_westmorland_furness"]

    assert source["kind"] == "lgsco_browser"
    assert source["browser_timeout"] >= 30
    assert source["fallback_to_date"] == "2026-9-7"
    urls = source["performance_fallback_urls"]
    assert len(urls) == 3
    assert any("/decisions/2023/u/Listing" in url for url in urls)
    assert any("/decisions/2024/u/Listing" in url for url in urls)
    assert any("/decisions/2025/u/Listing" in url for url in urls)
    assert all("Westmorland+and+Furness+Council" in url for url in urls)
