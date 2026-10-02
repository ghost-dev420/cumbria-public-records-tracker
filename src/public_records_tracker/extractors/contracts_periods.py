from __future__ import annotations

import json
from typing import Any

import duckdb

from ..models import Record
from ..structured import add_fact, upsert_entity


def _period(value: Any) -> tuple[str | None, str | None]:
    if not isinstance(value, dict):
        return None, None
    start = str(value.get("startDate") or "").strip() or None
    end = str(value.get("endDate") or "").strip() or None
    return start, end


def extract_contract_periods(
    *,
    con: duckdb.DuckDBPyConnection,
    record: Record,
    document_id: str,
    snapshot_id: str,
    source: dict,
) -> int:
    if "json" not in record.content_type.casefold():
        return 0
    try:
        release = json.loads(record.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    if not isinstance(release, dict):
        return 0

    tender = release.get("tender") or {}
    tender_period = _period(tender.get("contractPeriod") if isinstance(tender, dict) else None)
    ocid = str(release.get("ocid") or release.get("id") or document_id)
    awards = release.get("awards") or []
    count = 0

    for award_index, award in enumerate(awards, start=1):
        if not isinstance(award, dict):
            continue
        award_id = str(award.get("id") or f"award-{award_index}")
        award_title = str(
            award.get("title")
            or (tender.get("title") if isinstance(tender, dict) else "")
            or award_id
        ).strip()
        contract = upsert_entity(
            con,
            entity_type="CONTRACT",
            name=f"{ocid}:{award_id}",
            namespace="contracts_finder",
            metadata={
                "source_system": "Contracts Finder OCDS",
                "ocid": ocid,
                "award_id": award_id,
                "title": award_title,
                "date": award.get("date") or release.get("date"),
            },
        )
        award_period = _period(award.get("contractPeriod"))
        start, end = (
            award_period if any(award_period) else tender_period
        )
        if start:
            add_fact(
                con,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="CONTRACT_PERIOD",
                predicate="STARTS_ON",
                evidence_class=record.evidence_class.value,
                subject_entity_id=contract,
                value_text=start,
                locator=f"award {award_id} contract period start",
                metadata={"period_source": "award" if any(award_period) else "tender"},
            )
            count += 1
        if end:
            add_fact(
                con,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="CONTRACT_PERIOD",
                predicate="ENDS_ON",
                evidence_class=record.evidence_class.value,
                subject_entity_id=contract,
                value_text=end,
                locator=f"award {award_id} contract period end",
                metadata={"period_source": "award" if any(award_period) else "tender"},
            )
            count += 1
    return count
