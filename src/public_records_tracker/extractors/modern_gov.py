from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit

import duckdb
from bs4 import BeautifulSoup, Tag

from ..models import Record
from ..structured import add_fact, normalize_name, upsert_entity


COUNCILLOR_PREFIX = re.compile(
    r"^(?:Councillor|Cllr)\s+(?:(?:Dr|Mr|Mrs|Ms|Miss)\s+)?",
    re.IGNORECASE,
)
DECLARATION_ITEM_RE = re.compile(r"\b(?:declaration|disclosure)s?\b.*\binterest", re.IGNORECASE)
PERSON_DECLARED_RE = re.compile(
    r"\bCouncillor\s+(?:(?:Dr|Mr|Mrs|Ms|Miss)\s+)?"
    r"([A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){0,3})\s+declared\b"
)
DECISION_RE = re.compile(r"\b(?:RESOLVED|DECIDED|AGREED|RECOMMENDED)\b", re.IGNORECASE)
VOTE_RE = re.compile(r"\b(?:for|against|abstain(?:ed|tion|tions)?)\b", re.IGNORECASE)
AGENDA_NUMBER_RE = re.compile(r"^[A-Z0-9][A-Z0-9./-]{0,19}\.?$", re.IGNORECASE)
QUESTION_RE = re.compile(r"^\s*(\d+)\.\s*(.+)", re.DOTALL)


def _query(url: str) -> dict[str, list[str]]:
    return {key.casefold(): values for key, values in parse_qs(urlsplit(url).query).items()}


def _host_namespace(url: str) -> str:
    return urlsplit(url).netloc.casefold()


def _clean_person_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return COUNCILLOR_PREFIX.sub("", value).strip()


def _heading(soup: BeautifulSoup) -> str:
    node = soup.find(["h1", "h2"])
    return " ".join(node.stripped_strings) if node else ""


def _specific_heading(soup: BeautifulSoup, *generic_patterns: str) -> str:
    for node in soup.find_all(["h1", "h2"]):
        text = " ".join(node.stripped_strings).strip()
        if not text:
            continue
        if any(re.fullmatch(pattern, text, re.IGNORECASE) for pattern in generic_patterns):
            continue
        return text
    return _heading(soup)


def _string_after_label(soup: BeautifulSoup, label: str) -> str | None:
    strings = [re.sub(r"\s+", " ", text).strip() for text in soup.stripped_strings]
    wanted = label.casefold().rstrip(":")
    for index, text in enumerate(strings[:-1]):
        if text.casefold().rstrip(":") == wanted:
            return strings[index + 1]
    return None


def _section_items(soup: BeautifulSoup, heading_pattern: str) -> list[str]:
    heading = soup.find(
        lambda tag: isinstance(tag, Tag)
        and tag.name in {"h2", "h3", "h4"}
        and re.search(heading_pattern, " ".join(tag.stripped_strings), re.IGNORECASE)
    )
    if heading is None:
        return []
    values: list[str] = []
    for node in heading.find_all_next():
        if node is heading:
            continue
        if node.name in {"h2", "h3", "h4"}:
            break
        if node.name == "li":
            text = " ".join(node.stripped_strings).strip()
            if text and text not in values:
                values.append(text)
    return values


def _person_entity(
    con: duckdb.DuckDBPyConnection,
    *,
    name: str,
    url: str,
    uid: str | None = None,
) -> str:
    clean = _clean_person_name(name)
    metadata = {
        "source_system": "ModernGov",
        "modern_gov_host": _host_namespace(url),
    }
    if uid:
        metadata["modern_gov_uid"] = uid
    return upsert_entity(
        con,
        entity_type="PERSON",
        name=clean,
        namespace=_host_namespace(url),
        metadata=metadata,
    )


def _resolve_recorded_person(
    con: duckdb.DuckDBPyConnection,
    *,
    name: str,
    url: str,
) -> str | None:
    clean = _clean_person_name(name)
    if len(clean.split()) >= 2:
        return _person_entity(con, name=clean, url=url)
    normalized = normalize_name(clean)
    rows = con.execute(
        """SELECT entity_id, metadata_json
           FROM entities
           WHERE entity_type='PERSON'
             AND (normalized_name=? OR normalized_name LIKE ?)""",
        [normalized, f"% {normalized}"],
    ).fetchall()
    host = _host_namespace(url)
    matches: list[str] = []
    for entity_id, metadata_json in rows:
        try:
            metadata = json.loads(metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
        if metadata.get("modern_gov_host") == host:
            matches.append(entity_id)
    return matches[0] if len(matches) == 1 else None


def _committee_entity(
    con: duckdb.DuckDBPyConnection,
    *,
    name: str,
    url: str,
    modern_gov_id: str | None = None,
) -> str:
    metadata = {"source_system": "ModernGov"}
    if modern_gov_id:
        metadata["modern_gov_committee_id"] = modern_gov_id
    return upsert_entity(
        con,
        entity_type="COMMITTEE",
        name=name,
        namespace=_host_namespace(url),
        metadata=metadata,
    )


def _meeting_entity(
    con: duckdb.DuckDBPyConnection,
    *,
    name: str,
    url: str,
    meeting_id: str | None,
) -> str:
    stable_name = f"ModernGov meeting {meeting_id}" if meeting_id else name
    metadata = {"source_system": "ModernGov", "display_name": name}
    if meeting_id:
        metadata["modern_gov_meeting_id"] = meeting_id
    return upsert_entity(
        con,
        entity_type="MEETING",
        name=stable_name,
        namespace=_host_namespace(url),
        metadata=metadata,
    )


def _record_fact(
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
        subject_entity_id=subject,
        object_entity_id=object_,
        value_text=value,
        locator=locator,
        evidence_class=record.evidence_class.value,
        metadata=metadata,
    )


def _parse_committee(
    con: duckdb.DuckDBPyConnection,
    record: Record,
    document_id: str,
    snapshot_id: str,
    soup: BeautifulSoup,
) -> int:
    title = _specific_heading(soup, r"Committee details")
    if not title:
        return 0
    committee_id = (_query(record.url).get("id") or [None])[0]
    committee = _committee_entity(
        con,
        name=title,
        url=record.url,
        modern_gov_id=committee_id,
    )
    count = 0
    for anchor in soup.find_all("a", href=True):
        if "mguserinfo.aspx" not in anchor["href"].casefold():
            continue
        name = " ".join(anchor.stripped_strings).strip()
        if not name:
            continue
        uid = (_query(anchor["href"]).get("uid") or [None])[0]
        person = _person_entity(con, name=name, url=record.url, uid=uid)
        context = " ".join(anchor.parent.stripped_strings) if anchor.parent else name
        role_match = re.search(r"\(([^)]+)\)", context)
        role = role_match.group(1).strip() if role_match else "Member"
        _record_fact(
            con,
            record=record,
            document_id=document_id,
            snapshot_id=snapshot_id,
            fact_type="COMMITTEE_MEMBERSHIP",
            predicate="MEMBER_OF",
            subject=person,
            object_=committee,
            locator=f"committee membership: {name}",
            metadata={"role": role},
        )
        count += 1
    return count


def _parse_member(
    con: duckdb.DuckDBPyConnection,
    record: Record,
    document_id: str,
    snapshot_id: str,
    soup: BeautifulSoup,
) -> int:
    title = _specific_heading(soup, r"Councillor details")
    if not title:
        return 0
    uid = (_query(record.url).get("uid") or [None])[0]
    person = _person_entity(con, name=title, url=record.url, uid=uid)
    count = 0
    for label, entity_type, predicate in (
        ("Party", "POLITICAL_PARTY", "MEMBER_OF_PARTY"),
        ("Ward", "WARD", "REPRESENTS_WARD"),
    ):
        value = _string_after_label(soup, label)
        if not value:
            continue
        target = upsert_entity(con, entity_type=entity_type, name=value)
        _record_fact(
            con,
            record=record,
            document_id=document_id,
            snapshot_id=snapshot_id,
            fact_type=entity_type,
            predicate=predicate,
            subject=person,
            object_=target,
            locator=f"{label}: {value}",
        )
        count += 1

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        lower = href.casefold()
        text = " ".join(anchor.stripped_strings).strip()
        if not text:
            continue
        if "mgcommitteedetails.aspx" in lower:
            committee_id = (_query(href).get("id") or [None])[0]
            committee_name = re.sub(
                r"\s+\((?:chair|vice-chair|deputy chair|substitute)\)\s*$",
                "",
                text,
                flags=re.IGNORECASE,
            )
            committee = _committee_entity(
                con,
                name=committee_name,
                url=record.url,
                modern_gov_id=committee_id,
            )
            context = " ".join(anchor.parent.stripped_strings) if anchor.parent else text
            role_match = re.search(r"\(([^)]+)\)", context)
            _record_fact(
                con,
                record=record,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="COMMITTEE_MEMBERSHIP",
                predicate="MEMBER_OF",
                subject=person,
                object_=committee,
                locator=f"committee appointment: {text}",
                metadata={"role": role_match.group(1).strip() if role_match else "Member"},
            )
            count += 1
        elif "mgoutsidebodydetails.aspx" in lower:
            body = upsert_entity(con, entity_type="ORGANISATION", name=text)
            _record_fact(
                con,
                record=record,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="OUTSIDE_BODY_APPOINTMENT",
                predicate="APPOINTED_TO_OUTSIDE_BODY",
                subject=person,
                object_=body,
                locator=f"outside body: {text}",
            )
            count += 1

    for text in _section_items(soup, r"Appointments to outside bodies"):
        body = upsert_entity(con, entity_type="ORGANISATION", name=text)
        _record_fact(
            con,
            record=record,
            document_id=document_id,
            snapshot_id=snapshot_id,
            fact_type="OUTSIDE_BODY_APPOINTMENT",
            predicate="APPOINTED_TO_OUTSIDE_BODY",
            subject=person,
            object_=body,
            locator=f"outside body: {text}",
        )
        count += 1
    return count


def _previous_question(table: Tag) -> tuple[str | None, str | None]:
    for node in table.find_all_previous(["h2", "h3", "h4", "p", "div"], limit=12):
        text = " ".join(node.stripped_strings).strip()
        match = QUESTION_RE.match(text)
        if match:
            return match.group(1), match.group(2).strip()
    return None, None


def _member_column_values(table: Tag) -> list[str]:
    values: list[str] = []
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        value = " ".join(cells[0].stripped_strings).strip()
        if not value or value.casefold() == "member":
            continue
        if value.casefold() in {"none", "n/a", "not applicable", "nil"}:
            continue
        if value not in values:
            values.append(value)
    return values


def _parse_register(
    con: duckdb.DuckDBPyConnection,
    record: Record,
    document_id: str,
    snapshot_id: str,
    soup: BeautifulSoup,
) -> int:
    person_name = _specific_heading(soup, r"Register of interests")
    uid = (_query(record.url).get("uid") or [None])[0]
    person = _person_entity(con, name=person_name, url=record.url, uid=uid)
    page_text = " ".join(soup.stripped_strings)
    published_match = re.search(
        r"This register of interests was published on (.+?)(?:\.|More information|Printer friendly)",
        page_text,
        re.IGNORECASE,
    )
    published_at = published_match.group(1).strip() if published_match else None
    count = 0
    for table_index, table in enumerate(soup.find_all("table"), start=1):
        question_number, question = _previous_question(table)
        if not question_number:
            continue
        locator = f"register question {question_number}"
        if question_number == "4" or (question and "interest in land" in question.casefold()):
            value = "[REDACTED_FROM_STRUCTURED_DATA]"
            metadata = {
                "question": question,
                "question_number": question_number,
                "published_at": published_at,
                "redacted": True,
                "redaction_reason": "land/home-address field",
            }
        else:
            values = _member_column_values(table)
            if not values:
                continue
            value = "\n".join(values)[:10000]
            metadata = {
                "question": question,
                "question_number": question_number,
                "published_at": published_at,
                "redacted": False,
                "table_index": table_index,
            }
        _record_fact(
            con,
            record=record,
            document_id=document_id,
            snapshot_id=snapshot_id,
            fact_type="REGISTER_OF_INTEREST_ENTRY",
            predicate="DECLARED_INTEREST",
            subject=person,
            value=value,
            locator=locator,
            metadata=metadata,
        )
        count += 1
    return count


def _split_minutes(text: str) -> tuple[str, str | None]:
    match = re.search(r"\bMinutes:\s*", text, re.IGNORECASE)
    if not match:
        return text.strip(), None
    return text[: match.start()].strip(), text[match.end() :].strip()


def _parse_meeting(
    con: duckdb.DuckDBPyConnection,
    record: Record,
    document_id: str,
    snapshot_id: str,
    soup: BeautifulSoup,
) -> int:
    title = _specific_heading(soup, r"Agenda and (?:draft )?minutes", r"Agenda")
    if not title:
        return 0
    query = _query(record.url)
    meeting_id = (query.get("mid") or [None])[0]
    committee_id = (query.get("cid") or [None])[0]
    meeting = _meeting_entity(con, name=title, url=record.url, meeting_id=meeting_id)
    committee_name = title.split(" - ", 1)[0].strip()
    committee = _committee_entity(
        con,
        name=committee_name,
        url=record.url,
        modern_gov_id=committee_id,
    )
    count = 0
    _record_fact(
        con,
        record=record,
        document_id=document_id,
        snapshot_id=snapshot_id,
        fact_type="MEETING",
        predicate="MEETING_OF",
        subject=meeting,
        object_=committee,
        locator=title,
    )
    count += 1

    venue = _string_after_label(soup, "Venue")
    if venue:
        _record_fact(
            con,
            record=record,
            document_id=document_id,
            snapshot_id=snapshot_id,
            fact_type="MEETING_VENUE",
            predicate="VENUE",
            subject=meeting,
            value=venue,
            locator=f"Venue: {venue}",
        )
        count += 1

    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        item_number = " ".join(cells[0].stripped_strings).strip()
        item_text = " ".join(cells[1].stripped_strings).strip()
        if not item_number or not item_text or not AGENDA_NUMBER_RE.match(item_number):
            continue
        item_title, minutes = _split_minutes(item_text)
        item_name = f"{title} :: {item_number} {item_title[:180]}"
        agenda_item = upsert_entity(
            con,
            entity_type="AGENDA_ITEM",
            name=item_name,
            namespace=_host_namespace(record.url),
            metadata={"item_number": item_number, "meeting_id": meeting_id},
        )
        locator = f"agenda item {item_number}"
        _record_fact(
            con,
            record=record,
            document_id=document_id,
            snapshot_id=snapshot_id,
            fact_type="AGENDA_ITEM",
            predicate="AGENDA_ITEM_OF",
            subject=agenda_item,
            object_=meeting,
            value=item_title[:4000],
            locator=locator,
        )
        count += 1
        if not minutes:
            continue
        _record_fact(
            con,
            record=record,
            document_id=document_id,
            snapshot_id=snapshot_id,
            fact_type="MINUTES",
            predicate="MINUTES_TEXT",
            subject=agenda_item,
            value=minutes[:12000],
            locator=locator,
        )
        count += 1

        if DECLARATION_ITEM_RE.search(item_title):
            if re.search(
                r"\bno declarations? (?:of interest )?(?:were|was)? ?(?:received|made)?\b",
                minutes,
                re.IGNORECASE,
            ):
                _record_fact(
                    con,
                    record=record,
                    document_id=document_id,
                    snapshot_id=snapshot_id,
                    fact_type="DECLARATION_AT_MEETING",
                    predicate="DECLARATION_STATUS",
                    subject=meeting,
                    value="No declarations recorded in the minutes",
                    locator=locator,
                )
                count += 1
            for match in PERSON_DECLARED_RE.finditer(minutes):
                name = match.group(1).strip()
                person = _resolve_recorded_person(con, name=name, url=record.url)
                if person:
                    predicate = "DECLARED_INTEREST_AT"
                    subject = person
                    object_ = meeting
                else:
                    predicate = "DECLARATION_TEXT"
                    subject = meeting
                    object_ = None
                _record_fact(
                    con,
                    record=record,
                    document_id=document_id,
                    snapshot_id=snapshot_id,
                    fact_type="DECLARATION_AT_MEETING",
                    predicate=predicate,
                    subject=subject,
                    object_=object_,
                    value=minutes[:12000],
                    locator=locator,
                    metadata={
                        "agenda_item_entity_id": agenda_item,
                        "person_name_as_recorded": name,
                    },
                )
                count += 1

        if DECISION_RE.search(minutes):
            _record_fact(
                con,
                record=record,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="DECISION",
                predicate="DECISION_TEXT",
                subject=agenda_item,
                value=minutes[:12000],
                locator=locator,
            )
            count += 1
        if VOTE_RE.search(minutes) and re.search(r"\b(?:vote|voting|for:|against:)\b", minutes, re.I):
            _record_fact(
                con,
                record=record,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="VOTE_RECORD",
                predicate="VOTE_TEXT",
                subject=agenda_item,
                value=minutes[:12000],
                locator=locator,
            )
            count += 1
    return count


def _parse_attendance(
    con: duckdb.DuckDBPyConnection,
    record: Record,
    document_id: str,
    snapshot_id: str,
    soup: BeautifulSoup,
) -> int:
    meeting_id = (_query(record.url).get("id") or [None])[0]
    title = _heading(soup) or f"Meeting attendance {meeting_id or ''}".strip()
    meeting = _meeting_entity(con, name=title, url=record.url, meeting_id=meeting_id)
    count = 0
    for anchor in soup.find_all("a", href=True):
        if "mguserinfo.aspx" not in anchor["href"].casefold():
            continue
        name = " ".join(anchor.stripped_strings).strip()
        if not name:
            continue
        person = _person_entity(
            con,
            name=name,
            url=record.url,
            uid=(_query(anchor["href"]).get("uid") or [None])[0],
        )
        context = " ".join(anchor.parent.stripped_strings) if anchor.parent else name
        _record_fact(
            con,
            record=record,
            document_id=document_id,
            snapshot_id=snapshot_id,
            fact_type="MEETING_ATTENDANCE_ENTRY",
            predicate="ATTENDANCE_RECORDED_FOR",
            subject=person,
            object_=meeting,
            value=context[:2000],
            locator=f"attendance entry: {name}",
        )
        count += 1
    return count


def extract_modern_gov(
    *,
    con: duckdb.DuckDBPyConnection,
    record: Record,
    document_id: str,
    snapshot_id: str,
    source: dict,
) -> int:
    del source
    if "html" not in record.content_type.casefold():
        return 0
    soup = BeautifulSoup(record.body, "html.parser")
    path = urlsplit(record.url).path.casefold()
    if path.endswith("/mgcommitteedetails.aspx"):
        return _parse_committee(con, record, document_id, snapshot_id, soup)
    if path.endswith("/mguserinfo.aspx"):
        return _parse_member(con, record, document_id, snapshot_id, soup)
    if path.endswith("/mgdeclarationsubmission.aspx"):
        return _parse_register(con, record, document_id, snapshot_id, soup)
    if path.endswith("/ielistdocuments.aspx") or path.endswith("/celistdocuments.aspx"):
        return _parse_meeting(con, record, document_id, snapshot_id, soup)
    if path.endswith("/mgmeetingattendance.aspx"):
        return _parse_attendance(con, record, document_id, snapshot_id, soup)
    return 0
