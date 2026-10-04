from pathlib import Path

from public_records_tracker.config import load_sources


def test_android_full_registry_inherits_production_and_replaces_blocked_moderngov() -> None:
    sources = load_sources(Path("config/sources-android-full.yml"))
    by_id = {source["id"]: source for source in sources}

    # Normal production sources are inherited rather than copied into a second
    # registry that can drift.
    for source_id in (
        "cumberland_transparency",
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

    # HTML ModernGov entry points known to hit the Cloudflare challenge are
    # replaced by the public XML service.
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


def test_android_lgsco_uses_stable_council_performance_listings() -> None:
    sources = load_sources(Path("config/sources-android-full.yml"))
    by_id = {source["id"]: source for source in sources}

    for source_id in ("lgsco_cumberland", "lgsco_westmorland_furness"):
        source = by_id[source_id]
        urls = source["start_urls"]
        assert len(urls) >= 4
        assert all("/your-councils-performance/" in url for url in urls)
        assert all("/Decisions/SearchResults" not in url for url in urls)
        assert source["include_link_regex"] == r"/decisions/.+/\d{2}-\d{3}-\d{3}/?$"
        assert source["pagination_404_ends"] is True
        assert source["expect_facts"] is True
        assert source["minimum_fact_count"] == 1
