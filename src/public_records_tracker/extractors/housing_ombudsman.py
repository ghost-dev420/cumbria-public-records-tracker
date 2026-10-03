from __future__ import annotations

import re

import duckdb
from bs4 import BeautifulSoup, Tag

from ..models import Record
from ..structured import add_fact, upsert_entity


_DECISION_URL = re.compile(r"/decisions/[^/]+-(\d{6,})/?$", re.IGNORECASE)


def _next_section_text(heading: Tag, *, limit: int = 4000) -> str | None:
    chunks: list[str] = []
    sibling = heading.find_next_sibling()
    while sibling is not None:
        if isinstance(sibling, Tag) and sibling.name in {"h1", "h2", "h3", "h4"}:
            break
        if isinstance(sibling, Tag):
            value = sibling.get_text(" ", strip=True)
            if value:
                chunks.append(value)
        sibling = sibling.find_next_sibling()
    text = " ".join(chunks).strip()
    return text[:limit] if text else None


def _label_value(soup: BeautifulSoup, label: str) -> str | None:
    target = label.casefold()
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) >= 2 and cells[0].get_text(" ", strip=True).casefold() == target:
            return cells[1].get_text(" ", strip=True)
    text = soup.get_text("\n", strip=True)
    match = re.search(rf"(?im)^{re.escape(label)}\s+(.+?)\s*$", text)
    return match.group(1).strip() if match else None


def _finding_labels(determination: str | None) -> list[str]:
    """Extract coarse public finding categories without copying case narrative."""
    if not determination:
        return []
    text = determination.casefold()
    findings: list[str] = []
    checks = (
        ("severe maladministration", "Severe maladministration"),
        ("no maladministration", "No maladministration"),
        ("service failure", "Service failure"),
        ("reasonable redress", "Reasonable redress"),
    )
    for phrase, label in checks:
        if phrase in text and label not in findings:
            findings.append(label)
    if re.search(r"(?<!no )(?<!severe )\bmaladministration\b", text):
        findings.append("Maladministration")
    if any(
        phrase in text
        for phrase in (
            "outside jurisdiction",
            "outside the ombudsman's jurisdiction",
            "outside the ombudsman’s jurisdiction",
            "not within jurisdiction",
        )
    ):
        findings.append("Outside jurisdiction")
    return findings


def extract_housing_ombudsman(
    *,
    con: duckdb.DuckDBPyConnection,
    record: Record,
    document_id: str,
    snapshot_id: str,
    source: dict,
) -> int:
    del source
    url_match = _DECISION_URL.search(record.url)
    if not url_match or "html" not in record.content_type.casefold():
        return 0

    soup = BeautifulSoup(record.body, "html.parser")
    title = soup.find("h1")
    title_text = title.get_text(" ", strip=True) if title else record.title
    case_id = _label_value(soup, "Case ID") or url_match.group(1)
    decision_type = _label_value(soup, "Decision type")
    landlord_name = _label_value(soup, "Landlord") or "Home Group Limited"
    decision_date = _label_value(soup, "Date")

    determination = None
    for heading in soup.find_all(["h2", "h3", "h4"]):
        heading_text = heading.get_text(" ", strip=True).casefold()
        if "our decision" in heading_text or "determination" in heading_text:
            determination = _next_section_text(heading)
            if determination:
                break

    if determination is None:
        text = soup.get_text("\n", strip=True)
        legacy = re.search(
            r"(?is)(?:Determination|Our decision)\s*(.*?)(?:Reasons|Orders|Recommendations|$)",
            text,
        )
        determination = legacy.group(1).strip()[:4000] if legacy else None

    landlord = upsert_entity(
        con,
        entity_type="ORGANISATION",
        name=landlord_name,
        namespace="housing_ombudsman",
        metadata={"source_system": "Housing Ombudsman"},
    )
    case = upsert_entity(
        con,
        entity_type="OMBUDSMAN_CASE",
        name=f"Housing Ombudsman {case_id}",
        namespace="housing_ombudsman",
        metadata={"case_id": case_id, "title": title_text, "source_system": "Housing Ombudsman"},
    )

    facts = [
        ("HOUSING_OMBUDSMAN_CASE", "HAS_HOUSING_OMBUDSMAN_CASE", case_id, "Case ID"),
        ("HOUSING_OMBUDSMAN_DECISION_TYPE", "HAS_DECISION_TYPE", decision_type, "Decision type"),
        ("HOUSING_OMBUDSMAN_DECISION_DATE", "HAS_DECISION_DATE", decision_date, "Date"),
        (
            "HOUSING_OMBUDSMAN_DETERMINATION",
            "HAS_DETERMINATION",
            determination,
            "Our decision (determination)",
        ),
    ]
    count = 0
    for fact_type, predicate, value, locator in facts:
        if not value:
            continue
        add_fact(
            con,
            document_id=document_id,
            snapshot_id=snapshot_id,
            fact_type=fact_type,
            predicate=predicate,
            evidence_class=record.evidence_class.value,
            subject_entity_id=landlord,
            object_entity_id=case,
            value_text=value,
            locator=locator,
            metadata={"case_id": case_id, "source_system": "Housing Ombudsman"},
        )
        count += 1

    for finding in _finding_labels(determination):
        add_fact(
            con,
            document_id=document_id,
            snapshot_id=snapshot_id,
            fact_type="HOUSING_OMBUDSMAN_FINDING",
            predicate="HAS_FINDING",
            evidence_class=record.evidence_class.value,
            subject_entity_id=landlord,
            object_entity_id=case,
            value_text=finding,
            locator="Our decision (determination)",
            metadata={
                "case_id": case_id,
                "source_system": "Housing Ombudsman",
                "derived_from_public_determination": True,
            },
        )
        count += 1
    return count
