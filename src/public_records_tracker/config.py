from __future__ import annotations

from pathlib import Path

import yaml


_SOURCE_DEFAULTS = {
    "cumberland_transparency": {
        "extractors": ["payments"],
        "payment_payer_organisation_id": "cumberland_council",
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
    "westmorland_furness_spending": {
        "extractors": ["payments"],
        "payment_payer_organisation_id": "westmorland_furness_council",
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


def load_sources(path: Path) -> list[dict]:
    sources = _load_list(path, "sources")
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
            else:
                source.setdefault(key, value)
    return sources


def load_organisations(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return _load_list(path, "organisations")
