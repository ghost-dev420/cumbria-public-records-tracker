from __future__ import annotations

from pathlib import Path

import yaml


_SOURCE_DEFAULTS = {
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
            else:
                source.setdefault(key, value)
    return sources


def load_organisations(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return _load_list(path, "organisations")
