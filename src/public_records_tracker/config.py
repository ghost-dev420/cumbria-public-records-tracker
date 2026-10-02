from __future__ import annotations

from pathlib import Path

import yaml


def _load_list(path: Path, key: str) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = data.get(key) or []
    ids = [item.get("id") for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate {key.rstrip('s')} id in configuration")
    return items


def load_sources(path: Path) -> list[dict]:
    return _load_list(path, "sources")


def load_organisations(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return _load_list(path, "organisations")
