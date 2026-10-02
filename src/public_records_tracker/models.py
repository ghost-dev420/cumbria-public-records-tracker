from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EvidenceClass(StrEnum):
    OFFICIAL_RECORD = "OFFICIAL_RECORD"
    REGULATORY_FINDING = "REGULATORY_FINDING"
    COURT_RECORD = "COURT_RECORD"
    MEDIA_REPORT = "MEDIA_REPORT"
    PUBLIC_ALLEGATION = "PUBLIC_ALLEGATION"
    INFERENCE = "INFERENCE"
    UNVERIFIED_LEAD = "UNVERIFIED_LEAD"


@dataclass(slots=True)
class Record:
    source_id: str
    url: str
    title: str
    body: bytes
    content_type: str
    evidence_class: EvidenceClass
    published_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    status_code: int = 200
    etag: str | None = None
    last_modified: str | None = None
