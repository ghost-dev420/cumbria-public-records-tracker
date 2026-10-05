from __future__ import annotations

from pathlib import Path

import yaml


_SENSITIVE_PAYMENT_DENY_TERMS = [
    "private home",
    "private-home",
    "support related",
    "support-related",
    "supported individual",
    "supported individuals",
    "personal payment",
]

_CUMBERLAND_LEGACY_PAYER_RULES = [
    {
        "organisation_id": "allerdale_borough_council",
        "terms": [
            "allerdale spending",
            "allerdale_spending_",
            "allerdale expenditure",
            "allerdale_expenditure_",
        ],
    },
    {
        "organisation_id": "carlisle_city_council",
        "terms": [
            "carlisle spending",
            "carlisle_spending_",
            "carlisle expenditure",
            "carlisle_expenditure_",
        ],
    },
    {
        "organisation_id": "copeland_borough_council",
        "terms": [
            "copeland spending",
            "copeland_spending_",
            "copeland expenditure",
            "copeland_expenditure_",
        ],
    },
    {
        "organisation_id": "cumbria_county_council",
        "terms": [
            "cumbria county council spending",
            "cumbria_county_council_spending",
            "cumbria county council expenditure",
            "cumbria_county_council_expenditure",
        ],
    },
]

_WESTMORLAND_LEGACY_PAYER_RULES = [
    {
        "organisation_id": "barrow_borough_council",
        "terms": ["barrow borough", "barrow bc", "barrow b.c."],
    },
    {
        "organisation_id": "eden_district_council",
        "terms": ["eden district", "eden dc", "edc transparency"],
    },
    {
        "organisation_id": "south_lakeland_district_council",
        "terms": ["south lakeland", "sldc"],
    },
]


_SOURCE_DEFAULTS = {
    "contracts_finder": {
        "page_limit": 100,
    },
    "find_a_tender": {
        "page_limit": 100,
    },
    "cumberland_transparency": {
        "extractors": ["payments"],
        "payment_payer_organisation_id": "cumberland_council",
        "payment_payer_rules": _CUMBERLAND_LEGACY_PAYER_RULES,
        "allow_payment_csv_by_header": True,
        "payment_file_deny_terms": _SENSITIVE_PAYMENT_DENY_TERMS,
        "expect_facts": True,
        "minimum_fact_count": 1,
        "append_start_urls": [
            "https://www.cumberland.gov.uk/document-search?directorate_department_target_id=All&field_document_date_value=&field_document_date_value_1=&field_document_target_id=1589&field_media_document_description=&field_reference_value=",
            "https://www.cumberland.gov.uk/document-search?directorate_department_target_id=All&field_document_date_value=&field_document_date_value_1=&field_document_target_id=1433&field_media_document_description=&field_reference_value=&page=0",
            "https://www.cumberland.gov.uk/document-search?directorate_department_target_id=All&field_document_date_value=&field_document_date_value_1=&field_document_target_id=1329&field_media_document_description=&field_reference_value=",
            "https://www.cumberland.gov.uk/document-search?directorate_department_target_id=All&field_document_date_value=&field_document_date_value_1=&field_document_target_id=1287&field_media_document_description=&field_reference_value=&page=0",
        ],
        "publication_expectation": {
            "dataset": "spending_over_250",
            "label": "Spending over £250",
            "cadence_days": 45,
            "grace_days": 20,
        },
    },
    "cumberland_contract_register": {
        # The official register sometimes uses trading/short names while historic
        # spending files use the full legal-style supplier label. Keep the alias
        # narrow and source-specific rather than globally equating every use of
        # the word "group" with its shortened form.
        "contract_supplier_aliases": {
            "Tivoli": "TIVOLI GROUP LTD",
        },
    },
    "housing_ombudsman_home_group": {
        "extractors": ["housing_ombudsman"],
        "expect_facts": True,
        "minimum_fact_count": 1,
    },
    "lgsco_cumberland": {
        "extractors": ["lgsco"],
        "expect_facts": True,
        "minimum_fact_count": 1,
    },
    "lgsco_westmorland_furness": {
        "extractors": ["lgsco"],
        "expect_facts": True,
        "minimum_fact_count": 1,
    },
    "westmorland_furness_south_lakeland_grants": {
        "expect_facts": True,
        "minimum_fact_count": 1,
    },
    "westmorland_furness_spending": {
        "extractors": ["payments"],
        "payment_payer_organisation_id": "westmorland_furness_council",
        "payment_payer_rules": _WESTMORLAND_LEGACY_PAYER_RULES,
        "allow_payment_csv_by_header": True,
        "payment_file_deny_terms": _SENSITIVE_PAYMENT_DENY_TERMS,
        "expect_facts": True,
        "minimum_fact_count": 1,
        "publication_expectation": {
            "dataset": "spending_over_250",
            "label": "Spending over £250",
            "cadence_days": 45,
            "grace_days": 20,
        },
    },
}


def _load_list(path: Path, key: str) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = data.get(key) or []
    ids = [item.get("id") for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate {key.rstrip('s')} id in configuration")
    return items


def _merge_unique(items: list[dict], extras: list[dict], key: str) -> list[dict]:
    known = {item.get("id") for item in items}
    for item in extras:
        item_id = item.get("id")
        if item_id in known:
            raise ValueError(f"Duplicate {key.rstrip('s')} id across configuration files: {item_id}")
        items.append(item)
        known.add(item_id)
    return items


def _load_source_tree(path: Path, seen: set[Path] | None = None) -> list[dict]:
    """Load a source registry, optionally inheriting another registry.

    Overlay configs can declare ``include_sources_from`` and
    ``exclude_source_ids``. This lets constrained runtimes replace a small
    number of transport-specific sources without copying the production
    registry and letting the two versions drift apart.
    """

    resolved = path.resolve()
    chain = set(seen or ())
    if resolved in chain:
        raise ValueError(f"Recursive source configuration include: {path}")
    chain.add(resolved)

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    local = list(data.get("sources") or [])
    local_ids = [item.get("id") for item in local]
    if len(local_ids) != len(set(local_ids)):
        raise ValueError("Duplicate source id in configuration")

    include = data.get("include_sources_from")
    if not include:
        return local

    include_path = (path.parent / str(include)).resolve()
    config_root = path.parent.resolve()
    if include_path.parent != config_root:
        raise ValueError("include_sources_from must reference a file in the same config directory")

    inherited = _load_source_tree(include_path, chain)
    excluded = {str(value) for value in (data.get("exclude_source_ids") or [])}
    inherited = [source for source in inherited if str(source.get("id")) not in excluded]
    return _merge_unique(inherited, local, "sources")


def load_sources(path: Path) -> list[dict]:
    sources = _load_source_tree(path)
    supplemental = path.with_name("extra_sources.yml")
    if supplemental.exists():
        sources = _merge_unique(sources, _load_list(supplemental, "sources"), "sources")
    for source in sources:
        defaults = _SOURCE_DEFAULTS.get(str(source.get("id") or ""), {})
        for key, value in defaults.items():
            if key == "extractors":
                extractors = list(source.get("extractors") or [])
                for extractor in value:
                    if extractor not in extractors:
                        extractors.append(extractor)
                source["extractors"] = extractors
            elif key == "append_start_urls":
                start_urls = list(source.get("start_urls") or [])
                for start_url in value:
                    if start_url not in start_urls:
                        start_urls.append(start_url)
                source["start_urls"] = start_urls
            elif key == "contract_supplier_aliases":
                aliases = dict(value)
                configured = source.get("contract_supplier_aliases") or {}
                if isinstance(configured, dict):
                    aliases.update(configured)
                source["contract_supplier_aliases"] = aliases
            else:
                source.setdefault(key, value)
    return sources


def load_organisations(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return _load_list(path, "organisations")
