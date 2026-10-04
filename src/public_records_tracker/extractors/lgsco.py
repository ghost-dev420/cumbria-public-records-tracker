from __future__ import annotations

import re

import duckdb
from bs4 import BeautifulSoup

from ..models import Record
from ..structured import add_fact, upsert_entity


_DECISION_URL = re.compile(r"/decisions/.+/\d{2}-\d{3}-\d{3}/?$", re.IGNORECASE)
_CASE_HEADING = re.compile(
    r"(?m)^(?P<title>[^\n]+?)\s+\((?P<reference>\d{2}\s+\d{3}\s+\d{3})\)\s*$"
)
_RESULT_META = re.compile(
    r"(?m)^(?:Statement|Report)\s+"
    r"(?P<decision>Closed after initial enquiries|Not upheld|Upheld)\s+"
    r"(?P<category>.+?)\s+"
    r"(?P<date>\d{2}-[A-Za-z]{3}-\d{4})\s*$",
    re.IGNORECASE,
)


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


def _summary_from_segment(segment: str) -> str | None:
    lines = [line.strip() for line in segment.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if not line.casefold().startswith("summary:"):
            continue
        value = line.split(":", 1)[1].strip()
        if value:
            return value
        if index + 1 < len(lines):
            return lines[index + 1]
    return None


def _listing_cases(text: str) -> list[dict[str, str | None]]:
    matches = list(_CASE_HEADING.finditer(text))
    cases: list[dict[str, str | None]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = text[match.end() : end]
        metadata = _RESULT_META.search(segment)
        reference = match.group("reference").replace(" ", "-")
        cases.append(
            {
                "title": match.group("title").strip(),
                "reference": reference,
                "decision": metadata.group("decision").strip() if metadata else None,
                "category": metadata.group("category").strip() if metadata else None,
                "decision_date": metadata.group("date").strip() if metadata else None,
                "summary": _summary_from_segment(segment),
            }
        )
    return cases


def _add_case_facts(
    *,
    con: duckdb.DuckDBPyConnection,
    record: Record,
    document_id: str,
    snapshot_id: str,
    council: str | None,
    reference: str | None,
    title: str,
    decision: str | None,
    category: str | None,
    decision_date: str | None,
    summary: str | None,
    locator_prefix: str,
    listing_result: bool = False,
) -> int:
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
        (
            "OMBUDSMAN_DECISION_DATE",
            "HAS_OMBUDSMAN_DECISION_DATE",
            decision_date,
            "Decision date",
        ),
        ("OMBUDSMAN_SUMMARY", "HAS_OMBUDSMAN_SUMMARY", summary, "summary"),
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
            locator=f"{locator_prefix}: {locator}",
            metadata={
                "reference": reference,
                "source_system": "LGSCO",
                "listing_result": listing_result,
            },
        )
        count += 1
    return count


def extract_lgsco(
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
    text = soup.get_text("\n", strip=True)
    council = _organisation_entity(con, source)

    if _DECISION_URL.search(record.url):
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
        return _add_case_facts(
            con=con,
            record=record,
            document_id=document_id,
            snapshot_id=snapshot_id,
            council=council,
            reference=reference,
            title=title,
            decision=decision,
            category=category,
            decision_date=decision_date,
            summary=summary,
            locator_prefix="decision page",
        )

    if not bool((record.metadata or {}).get("parse_listing_results")):
        return 0

    count = 0
    for case in _listing_cases(text):
        if not any(
            case.get(key)
            for key in ("reference", "decision", "category", "decision_date", "summary")
        ):
            continue
        count += _add_case_facts(
            con=con,
            record=record,
            document_id=document_id,
            snapshot_id=snapshot_id,
            council=council,
            reference=str(case["reference"]) if case.get("reference") else None,
            title=str(case["title"] or case["reference"] or "LGSCO case"),
            decision=str(case["decision"]) if case.get("decision") else None,
            category=str(case["category"]) if case.get("category") else None,
            decision_date=(
                str(case["decision_date"]) if case.get("decision_date") else None
            ),
            summary=str(case["summary"]) if case.get("summary") else None,
            locator_prefix=f"search result {case.get('reference') or ''}".strip(),
            listing_result=True,
        )
    return count
