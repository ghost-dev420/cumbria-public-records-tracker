from __future__ import annotations

import re

import duckdb
from bs4 import BeautifulSoup

from ..models import Record
from ..structured import add_fact, upsert_entity


_DECISION_URL = re.compile(r"/decisions/.+/\d{2}-\d{3}-\d{3}/?$", re.IGNORECASE)


def _organisation_entity(con: duckdb.DuckDBPyConnection, source: dict) -> str | None:
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
        metadata={"organisation_id": organisation_id, "source_system": "LGSCO"},
    )


def _value(text: str, label: str) -> str | None:
    match = re.search(rf"(?im)^{re.escape(label)}\s*:\s*(.+?)\s*$", text)
    return match.group(1).strip() if match else None


def extract_lgsco(
    *,
    con: duckdb.DuckDBPyConnection,
    record: Record,
    document_id: str,
    snapshot_id: str,
    source: dict,
) -> int:
    if not _DECISION_URL.search(record.url):
        return 0
    if "html" not in record.content_type.casefold():
        return 0

    soup = BeautifulSoup(record.body, "html.parser")
    text = soup.get_text("\n", strip=True)
    heading = soup.find("h1")
    title = heading.get_text(" ", strip=True) if heading else record.title
    reference_match = re.search(r"\((\d{2}\s+\d{3}\s+\d{3})\)", title)
    reference = reference_match.group(1).replace(" ", "-") if reference_match else None
    category = _value(text, "Category")
    decision = _value(text, "Decision")
    decision_date = _value(text, "Decision date")
    summary_match = re.search(r"(?im)^Summary:\s*(.+?)\s*$", text)
    summary = summary_match.group(1).strip() if summary_match else None
    if not any((reference, category, decision, decision_date, summary)):
        return 0

    council = _organisation_entity(con, source)
    case_name = reference or title
    case = upsert_entity(
        con,
        entity_type="OMBUDSMAN_CASE",
        name=case_name,
        namespace="lgsco",
        metadata={"source_system": "LGSCO", "reference": reference},
    )

    values = [
        ("OMBUDSMAN_CASE", "HAS_OMBUDSMAN_CASE", reference or title, "case reference"),
        ("OMBUDSMAN_DECISION", "HAS_OMBUDSMAN_DECISION", decision, "Decision"),
        ("OMBUDSMAN_CATEGORY", "HAS_OMBUDSMAN_CATEGORY", category, "Category"),
        ("OMBUDSMAN_DECISION_DATE", "HAS_OMBUDSMAN_DECISION_DATE", decision_date, "Decision date"),
        ("OMBUDSMAN_SUMMARY", "HAS_OMBUDSMAN_SUMMARY", summary, "final decision summary"),
    ]
    count = 0
    for fact_type, predicate, value, locator in values:
        if not value:
            continue
        add_fact(
            con,
            document_id=document_id,
            snapshot_id=snapshot_id,
            fact_type=fact_type,
            predicate=predicate,
            evidence_class=record.evidence_class.value,
            subject_entity_id=council,
            object_entity_id=case,
            value_text=value,
            locator=locator,
            metadata={"reference": reference, "source_system": "LGSCO"},
        )
        count += 1
    return count
