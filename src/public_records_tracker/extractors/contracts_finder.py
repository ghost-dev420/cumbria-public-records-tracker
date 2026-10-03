from __future__ import annotations

import json
from typing import Any

import duckdb

from ..models import Record
from ..resolution import add_identifier
from ..structured import add_fact, upsert_entity


def _source_system(record: Record, source: dict) -> str:
    return str(
        record.metadata.get("source_system")
        or source.get("name")
        or source.get("id")
        or "UK procurement OCDS"
    )


def _party_entity(
    con: duckdb.DuckDBPyConnection,
    party: dict[str, Any],
    *,
    fallback_type: str,
    source_system: str,
) -> str | None:
    name = str(party.get("name") or "").strip()
    if not name:
        return None
    identifiers: list[dict[str, str]] = []
    identifier = party.get("identifier") or {}
    if isinstance(identifier, dict) and identifier.get("id"):
        identifiers.append(
            {
                "scheme": str(identifier.get("scheme") or "unknown"),
                "id": str(identifier["id"]),
            }
        )
    for extra in party.get("additionalIdentifiers") or []:
        if isinstance(extra, dict) and extra.get("id"):
            identifiers.append(
                {
                    "scheme": str(extra.get("scheme") or "unknown"),
                    "id": str(extra["id"]),
                }
            )
    # Keep the historic namespace stable so existing entity IDs continue to
    # reconcile across both UK OCDS feeds. The source-system metadata records
    # which feed actually supplied the observation.
    entity_id = upsert_entity(
        con,
        entity_type=fallback_type,
        name=name,
        namespace="contracts_finder",
        metadata={
            "source_system": source_system,
            "identifiers": identifiers,
        },
    )
    for item in identifiers:
        add_identifier(
            con,
            entity_id=entity_id,
            scheme=item["scheme"],
            identifier=item["id"],
            source=source_system,
        )
    return entity_id


def _fact(
    con: duckdb.DuckDBPyConnection,
    *,
    record: Record,
    document_id: str,
    snapshot_id: str,
    fact_type: str,
    predicate: str,
    subject: str | None = None,
    object_: str | None = None,
    value: str | None = None,
    locator: str | None = None,
    metadata: dict | None = None,
) -> str:
    return add_fact(
        con,
        document_id=document_id,
        snapshot_id=snapshot_id,
        fact_type=fact_type,
        predicate=predicate,
        evidence_class=record.evidence_class.value,
        subject_entity_id=subject,
        object_entity_id=object_,
        value_text=value,
        locator=locator,
        metadata=metadata,
    )


def extract_contracts_finder(
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
    if not isinstance(release, dict) or not (release.get("ocid") or release.get("awards")):
        return 0

    source_system = _source_system(record, source)
    parties: dict[str, dict[str, Any]] = {}
    buyers: list[str] = []
    suppliers: dict[str, str] = {}
    for party in release.get("parties") or []:
        if not isinstance(party, dict):
            continue
        party_id = str(party.get("id") or party.get("name") or "")
        if party_id:
            parties[party_id] = party
        roles = {str(role).casefold() for role in party.get("roles") or []}
        if "buyer" in roles:
            entity = _party_entity(
                con, party, fallback_type="ORGANISATION", source_system=source_system
            )
            if entity:
                buyers.append(entity)
        if "supplier" in roles:
            entity = _party_entity(
                con, party, fallback_type="SUPPLIER", source_system=source_system
            )
            if entity:
                suppliers[party_id] = entity

    buyer = release.get("buyer") or {}
    if isinstance(buyer, dict) and buyer.get("name"):
        buyer_entity = _party_entity(
            con, buyer, fallback_type="ORGANISATION", source_system=source_system
        )
        if buyer_entity and buyer_entity not in buyers:
            buyers.append(buyer_entity)

    tender = release.get("tender") or {}
    tender_title = str(tender.get("title") or record.title).strip()
    ocid = str(release.get("ocid") or release.get("id") or document_id)
    release_date = str(release.get("date") or record.published_at or "") or None
    count = 0

    awards = release.get("awards") or []
    if not awards:
        awards = [{"id": "notice", "title": tender_title, "suppliers": []}]

    for award_index, award in enumerate(awards, start=1):
        if not isinstance(award, dict):
            continue
        award_id = str(award.get("id") or f"award-{award_index}")
        award_title = str(award.get("title") or tender_title or award_id).strip()
        contract = upsert_entity(
            con,
            entity_type="CONTRACT",
            name=f"{ocid}:{award_id}",
            namespace="contracts_finder",
            metadata={
                "source_system": source_system,
                "ocid": ocid,
                "award_id": award_id,
                "title": award_title,
                "date": award.get("date") or release_date,
            },
        )
        _fact(
            con,
            record=record,
            document_id=document_id,
            snapshot_id=snapshot_id,
            fact_type="CONTRACT_TITLE",
            predicate="HAS_TITLE",
            subject=contract,
            value=award_title,
            locator=f"award {award_id}",
        )
        count += 1

        for buyer_entity in buyers:
            _fact(
                con,
                record=record,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="CONTRACT_BUYER",
                predicate="BUYER_OF_CONTRACT",
                subject=buyer_entity,
                object_=contract,
                locator=f"award {award_id} buyer",
            )
            count += 1

        award_suppliers: list[str] = []
        for supplier in award.get("suppliers") or []:
            if not isinstance(supplier, dict):
                continue
            supplier_key = str(supplier.get("id") or supplier.get("name") or "")
            entity = suppliers.get(supplier_key)
            if entity is None:
                entity = _party_entity(
                    con,
                    supplier,
                    fallback_type="SUPPLIER",
                    source_system=source_system,
                )
            if entity and entity not in award_suppliers:
                award_suppliers.append(entity)
        for supplier_entity in award_suppliers:
            _fact(
                con,
                record=record,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="CONTRACT_SUPPLIER",
                predicate="SUPPLIER_TO_CONTRACT",
                subject=supplier_entity,
                object_=contract,
                locator=f"award {award_id} supplier",
            )
            count += 1

        value = award.get("value") or {}
        if isinstance(value, dict) and value.get("amount") is not None:
            amount = str(value.get("amount"))
            currency = str(value.get("currency") or "GBP")
            _fact(
                con,
                record=record,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="CONTRACT_VALUE",
                predicate="HAS_VALUE",
                subject=contract,
                value=amount,
                locator=f"award {award_id} value",
                metadata={"currency": currency},
            )
            count += 1

        award_date = award.get("date") or release_date
        if award_date:
            _fact(
                con,
                record=record,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="CONTRACT_DATE",
                predicate="HAS_AWARD_DATE",
                subject=contract,
                value=str(award_date),
                locator=f"award {award_id} date",
            )
            count += 1
    return count
