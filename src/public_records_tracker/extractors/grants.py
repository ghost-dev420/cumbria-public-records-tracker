from __future__ import annotations

import hashlib
import re

import duckdb
from bs4 import BeautifulSoup

from ..models import Record
from ..structured import add_fact, upsert_entity


_AMOUNT_RE = re.compile(r"£\s*([0-9][0-9,]*(?:\.\d{1,2})?)")


def _council_entity(con: duckdb.DuckDBPyConnection, source: dict) -> str | None:
    organisation_ids = list(source.get("organisation_ids") or [])
    if not organisation_ids:
        return None
    organisation_id = organisation_ids[0]
    row = con.execute(
        "SELECT name FROM organisations WHERE organisation_id=?", [organisation_id]
    ).fetchone()
    if not row:
        return None
    return upsert_entity(
        con,
        entity_type="COUNCIL",
        name=str(row[0]),
        namespace=f"organisation:{organisation_id}",
        metadata={"organisation_id": organisation_id, "source_system": source.get("name")},
    )


def _split_award(text: str) -> tuple[str, str | None, str] | None:
    amount_match = _AMOUNT_RE.search(text)
    if not amount_match:
        return None
    amount = amount_match.group(1).replace(",", "")
    prefix = text[: amount_match.start()].strip().rstrip("-:; ")
    if not prefix:
        return None
    parts = re.split(r"\s+(?:-|–|—)\s+", prefix, maxsplit=1)
    recipient = parts[0].strip()
    purpose = parts[1].strip() if len(parts) > 1 else None
    if not recipient:
        return None
    return recipient, purpose, amount


def extract_grant_awards(
    *,
    con: duckdb.DuckDBPyConnection,
    record: Record,
    document_id: str,
    snapshot_id: str,
    source: dict,
) -> int:
    if "html" not in record.content_type.casefold():
        return 0
    soup = BeautifulSoup(record.body, "html.parser")
    council = _council_entity(con, source)
    count = 0

    for heading in soup.find_all(["h2", "h3", "h4"]):
        heading_text = heading.get_text(" ", strip=True)
        if "successful grants" not in heading_text.casefold():
            continue
        sibling = heading.find_next_sibling()
        while sibling is not None and sibling.name not in {"h2", "h3", "h4"}:
            if sibling.name in {"ul", "ol"}:
                for item_index, li in enumerate(sibling.find_all("li", recursive=False), start=1):
                    raw = li.get_text(" ", strip=True)
                    parsed = _split_award(raw)
                    if parsed is None:
                        continue
                    recipient_name, purpose, amount = parsed
                    recipient = upsert_entity(
                        con,
                        entity_type="ORGANISATION",
                        name=recipient_name,
                        namespace="grant_recipient",
                        metadata={"source_system": source.get("name", source["id"])},
                    )
                    award_key = hashlib.sha256(
                        f"{heading_text}\0{recipient_name}\0{amount}\0{purpose or ''}".encode()
                    ).hexdigest()[:20]
                    grant = upsert_entity(
                        con,
                        entity_type="GRANT",
                        name=f"{heading_text}: {recipient_name} [{award_key}]",
                        namespace=source["id"],
                        metadata={
                            "round": heading_text,
                            "recipient": recipient_name,
                            "amount_gbp": amount,
                            "purpose": purpose,
                        },
                    )
                    locator = f"{heading_text}; list item {item_index}"
                    add_fact(
                        con,
                        document_id=document_id,
                        snapshot_id=snapshot_id,
                        fact_type="GRANT_AWARD",
                        predicate="AWARDED_GRANT",
                        evidence_class=record.evidence_class.value,
                        subject_entity_id=council,
                        object_entity_id=grant,
                        value_text=amount,
                        locator=locator,
                        metadata={"currency": "GBP", "round": heading_text, "purpose": purpose},
                    )
                    add_fact(
                        con,
                        document_id=document_id,
                        snapshot_id=snapshot_id,
                        fact_type="GRANT_RECIPIENT",
                        predicate="RECIPIENT_OF_GRANT",
                        evidence_class=record.evidence_class.value,
                        subject_entity_id=recipient,
                        object_entity_id=grant,
                        value_text=amount,
                        locator=locator,
                        metadata={"currency": "GBP", "round": heading_text, "purpose": purpose},
                    )
                    count += 2
            sibling = sibling.find_next_sibling()
    return count
