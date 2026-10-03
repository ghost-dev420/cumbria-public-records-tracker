from __future__ import annotations

from xml.etree import ElementTree

import duckdb

from ..models import Record
from ..structured import upsert_entity
from .modern_gov import (
    _committee_entity,
    _host_namespace,
    _meeting_entity,
    _person_entity,
    _record_fact,
)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split()).strip()
    return text or None


def _child_text(node: ElementTree.Element, *names: str) -> str | None:
    wanted = {name.casefold() for name in names}
    for child in node.iter():
        if child is node:
            continue
        if _local_name(child.tag) in wanted:
            value = _clean(child.text)
            if value:
                return value
    return None


def _nodes(root: ElementTree.Element, *names: str):
    wanted = {name.casefold() for name in names}
    for node in root.iter():
        if _local_name(node.tag) in wanted:
            yield node


def _bool_text(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.casefold()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    return None


def _operation(record: Record) -> str:
    operation = str(record.metadata.get("operation") or "")
    if operation:
        return operation.casefold()
    path = record.url.split("?", 1)[0].rstrip("/")
    return path.rsplit("/", 1)[-1].casefold()


def _extract_committees(
    con: duckdb.DuckDBPyConnection,
    record: Record,
    document_id: str,
    snapshot_id: str,
    root: ElementTree.Element,
) -> int:
    count = 0
    for node in _nodes(root, "committee"):
        committee_id = _child_text(node, "committeeid", "id")
        title = _child_text(node, "committeetitle", "title", "name")
        if not title:
            continue
        committee = _committee_entity(
            con,
            name=title,
            url=record.url,
            modern_gov_id=committee_id,
        )
        metadata = {
            "modern_gov_committee_id": committee_id,
            "category": _child_text(node, "committeecategory", "category"),
            "deleted": _bool_text(_child_text(node, "committeedeleted", "deleted")),
            "expired": _bool_text(_child_text(node, "committeeexpired", "expired")),
        }
        _record_fact(
            con,
            record=record,
            document_id=document_id,
            snapshot_id=snapshot_id,
            fact_type="COMMITTEE_RECORD",
            predicate="COMMITTEE_LISTED",
            subject=committee,
            value=title,
            locator=f"committee {committee_id or title}",
            metadata=metadata,
        )
        count += 1
    return count


def _extract_councillors(
    con: duckdb.DuckDBPyConnection,
    record: Record,
    document_id: str,
    snapshot_id: str,
    root: ElementTree.Element,
) -> int:
    count = 0
    namespace = _host_namespace(record.url)
    for ward_node in _nodes(root, "ward"):
        ward_title = _child_text(ward_node, "wardtitle", "wardname")
        if not ward_title:
            continue
        ward = upsert_entity(
            con,
            entity_type="WARD",
            name=ward_title,
            namespace=namespace,
            metadata={"source_system": "ModernGov"},
        )
        for councillor_node in _nodes(ward_node, "councillor"):
            councillor_id = _child_text(councillor_node, "councillorid", "userid", "id")
            name = _child_text(councillor_node, "fullusername", "fullname", "name")
            if not name:
                continue
            person = _person_entity(
                con,
                name=name,
                url=record.url,
                uid=councillor_id,
            )
            _record_fact(
                con,
                record=record,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="WARD",
                predicate="REPRESENTS_WARD",
                subject=person,
                object_=ward,
                locator=f"ward: {ward_title}",
                metadata={"modern_gov_user_id": councillor_id},
            )
            count += 1

            party_title = _child_text(
                councillor_node,
                "politicalpartytitle",
                "partytitle",
                "party",
            )
            if party_title:
                party = upsert_entity(con, entity_type="POLITICAL_PARTY", name=party_title)
                _record_fact(
                    con,
                    record=record,
                    document_id=document_id,
                    snapshot_id=snapshot_id,
                    fact_type="POLITICAL_PARTY",
                    predicate="MEMBER_OF_PARTY",
                    subject=person,
                    object_=party,
                    locator=f"party: {party_title}",
                )
                count += 1

            key_posts = _child_text(councillor_node, "keyposts", "keypost")
            if key_posts:
                _record_fact(
                    con,
                    record=record,
                    document_id=document_id,
                    snapshot_id=snapshot_id,
                    fact_type="PUBLIC_ROLE",
                    predicate="PUBLIC_ROLE_TEXT",
                    subject=person,
                    value=key_posts[:4000],
                    locator="ModernGov key posts",
                )
                count += 1

            for term in _nodes(councillor_node, "termofoffice"):
                start = _child_text(term, "startdate")
                end = _child_text(term, "enddate")
                if not start and not end:
                    continue
                _record_fact(
                    con,
                    record=record,
                    document_id=document_id,
                    snapshot_id=snapshot_id,
                    fact_type="TERM_OF_OFFICE",
                    predicate="TERM_OF_OFFICE",
                    subject=person,
                    value=f"{start or 'unspecified'} to {end or 'unspecified'}",
                    locator="term of office",
                    metadata={"start_date": start, "end_date": end},
                )
                count += 1

            # Deliberately do not structure work/home address, email or phone
            # fields even when the public XML response contains them.
    return count


def _extract_parishes(
    con: duckdb.DuckDBPyConnection,
    record: Record,
    document_id: str,
    snapshot_id: str,
    root: ElementTree.Element,
) -> int:
    count = 0
    namespace = _host_namespace(record.url)
    candidates = list(_nodes(root, "parishcouncil", "parish"))
    for node in candidates:
        parish_id = _child_text(node, "parishcouncilid", "parishid", "id")
        title = _child_text(
            node,
            "parishcounciltitle",
            "parishtitle",
            "title",
            "name",
        )
        if not title:
            continue
        parish = upsert_entity(
            con,
            entity_type="PARISH_COUNCIL",
            name=title,
            namespace=namespace,
            metadata={
                "source_system": "ModernGov",
                "modern_gov_parish_id": parish_id,
            },
        )
        _record_fact(
            con,
            record=record,
            document_id=document_id,
            snapshot_id=snapshot_id,
            fact_type="PARISH_COUNCIL_RECORD",
            predicate="PARISH_COUNCIL_LISTED",
            subject=parish,
            value=title,
            locator=f"parish council {parish_id or title}",
            metadata={"modern_gov_parish_id": parish_id},
        )
        count += 1
    return count


def _meeting_candidates(root: ElementTree.Element):
    candidates = list(_nodes(root, "meeting", "calendarevent", "event"))
    if candidates:
        return candidates
    if _child_text(root, "meetingid", "meetingtitle", "meetingdate"):
        return [root]
    return []


def _extract_meetings(
    con: duckdb.DuckDBPyConnection,
    record: Record,
    document_id: str,
    snapshot_id: str,
    root: ElementTree.Element,
) -> int:
    count = 0
    seen: set[str] = set()
    for node in _meeting_candidates(root):
        meeting_id = _child_text(node, "meetingid", "meeting_id", "id")
        title = _child_text(
            node,
            "meetingtitle",
            "eventtitle",
            "title",
            "name",
        )
        meeting_date = _child_text(
            node,
            "meetingdate",
            "eventdate",
            "date",
            "startdate",
            "startdatetime",
        )
        committee_id = _child_text(node, "committeeid", "committeeref")
        committee_title = _child_text(node, "committeetitle", "committee")
        venue = _child_text(node, "venue", "meetingvenue", "location")
        stable_key = meeting_id or f"{title}|{meeting_date}|{committee_title}"
        if not stable_key or stable_key in seen:
            continue
        seen.add(stable_key)
        display = title or " - ".join(value for value in (committee_title, meeting_date) if value)
        if not display:
            continue
        meeting = _meeting_entity(
            con,
            name=display,
            url=record.url,
            meeting_id=meeting_id,
        )
        if committee_title:
            committee = _committee_entity(
                con,
                name=committee_title,
                url=record.url,
                modern_gov_id=committee_id,
            )
            _record_fact(
                con,
                record=record,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="MEETING",
                predicate="MEETING_OF",
                subject=meeting,
                object_=committee,
                locator=f"meeting {meeting_id or display}",
            )
            count += 1
        else:
            _record_fact(
                con,
                record=record,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="MEETING",
                predicate="MEETING_LISTED",
                subject=meeting,
                value=display,
                locator=f"meeting {meeting_id or display}",
            )
            count += 1
        if meeting_date:
            _record_fact(
                con,
                record=record,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="MEETING_DATE",
                predicate="MEETING_DATE",
                subject=meeting,
                value=meeting_date,
                locator=f"meeting date: {meeting_date}",
            )
            count += 1
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
                locator=f"venue: {venue}",
            )
            count += 1
    return count


def extract_modern_gov_xml(
    *,
    con: duckdb.DuckDBPyConnection,
    record: Record,
    document_id: str,
    snapshot_id: str,
    source: dict,
) -> int:
    del source
    if "xml" not in record.content_type.casefold() and not record.body.lstrip().startswith(b"<?xml"):
        return 0
    try:
        root = ElementTree.fromstring(record.body)
    except ElementTree.ParseError:
        return 0

    operation = _operation(record)
    if operation == "getcommittees":
        return _extract_committees(con, record, document_id, snapshot_id, root)
    if operation == "getcouncillorsbyward":
        return _extract_councillors(con, record, document_id, snapshot_id, root)
    if operation == "getparishcouncils":
        return _extract_parishes(con, record, document_id, snapshot_id, root)
    if operation in {"getcalendarevents", "getallmeetingsbydate", "getmeetings", "getmeeting"}:
        return _extract_meetings(con, record, document_id, snapshot_id, root)
    return 0
