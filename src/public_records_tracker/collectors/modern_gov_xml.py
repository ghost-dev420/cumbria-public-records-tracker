from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urljoin
from xml.etree import ElementTree

from ..models import EvidenceClass, Record


DEFAULT_OPERATIONS = ["GetCommittees", "GetCouncillorsByWard"]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _meeting_ids(payload: bytes) -> list[str]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return []
    values: list[str] = []
    for node in root.iter():
        if _local_name(node.tag) not in {"meetingid", "meeting_id"}:
            continue
        value = (node.text or "").strip()
        if value and value.isdigit() and value not in values:
            values.append(value)
    return values


class ModernGovXmlCollector:
    """Collect ModernGov's public XML web-service endpoints.

    The collector uses the documented/public ``mgWebService.asmx`` HTTP GET
    surface. It does not attempt to bypass the Cloudflare challenge protecting
    the interactive HTML pages.
    """

    def __init__(self, source: dict, client, *, page_limit: int | None = None) -> None:
        self.source = source
        self.client = client
        self.page_limit = page_limit or int(source.get("page_limit", 500))
        self.errors: list[Exception] = []

    def _base_url(self) -> str:
        explicit = str(self.source.get("api_base_url") or "").strip()
        if explicit:
            return explicit.rstrip("/") + "/"
        start_urls = list(self.source.get("start_urls") or [])
        if not start_urls:
            raise ValueError("ModernGov XML source requires api_base_url or start_urls")
        first = str(start_urls[0])
        return first.split("/mg", 1)[0].rstrip("/") + "/"

    def _request(self, operation: str, params: dict[str, object] | None = None):
        base = self._base_url()
        url = urljoin(base, f"mgWebService.asmx/{operation}")
        return self.client.get(
            url,
            params=params,
            headers={"Accept": "application/xml,text/xml;q=0.9,*/*;q=0.1"},
        )

    def _record(
        self,
        operation: str,
        response,
        request_params: dict[str, object] | None = None,
    ) -> Record:
        content_type = response.headers.get("content-type", "text/xml").split(";", 1)[0]
        metadata: dict[str, object] = {
            "modern_gov_xml": True,
            "operation": operation,
        }
        if request_params:
            metadata["request_params"] = {
                str(key): str(value) for key, value in request_params.items()
            }
        return Record(
            source_id=self.source["id"],
            url=str(response.url),
            title=f"ModernGov XML API: {operation}",
            body=response.content,
            content_type=content_type,
            evidence_class=EvidenceClass(self.source["evidence_class"]),
            status_code=response.status_code,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            metadata=metadata,
        )

    def _calendar_params(self) -> dict[str, object]:
        today = date.today()
        start = today - timedelta(days=int(self.source.get("meeting_days_back", 730)))
        end = today + timedelta(days=int(self.source.get("meeting_days_forward", 365)))
        return {
            "bGlobalCalendar": "true",
            "sDateStart": start.strftime("%d/%m/%Y"),
            "sDateEnd": end.strftime("%d/%m/%Y"),
            "lUserId": 0,
        }

    def collect(self):
        operations = list(self.source.get("api_operations") or DEFAULT_OPERATIONS)
        emitted = 0
        meeting_ids: list[str] = []

        for operation in operations:
            if emitted >= self.page_limit:
                break
            params = self._calendar_params() if operation == "GetCalendarEvents" else None
            print(f"  ModernGov XML {operation}...")
            try:
                response = self._request(operation, params=params)
            except Exception as exc:
                self.errors.append(exc)
                print(f"WARN ModernGov XML fetch failed {operation}: {exc}")
                continue
            emitted += 1
            record = self._record(operation, response, params)
            yield record
            if operation in {"GetCalendarEvents", "GetAllMeetingsByDate", "GetMeetings"}:
                for meeting_id in _meeting_ids(response.content):
                    if meeting_id not in meeting_ids:
                        meeting_ids.append(meeting_id)

        if not self.source.get("expand_meetings", False) or not meeting_ids:
            return

        print(f"  ModernGov XML expanding {len(meeting_ids)} meeting records...")
        total = len(meeting_ids)
        for index, meeting_id in enumerate(meeting_ids, start=1):
            if emitted >= self.page_limit:
                print(
                    f"  ModernGov XML meeting expansion stopped at page_limit={self.page_limit}."
                )
                break
            params = {"lMeetingId": meeting_id}
            try:
                response = self._request("GetMeeting", params=params)
            except Exception as exc:
                self.errors.append(exc)
                print(f"WARN ModernGov XML meeting fetch failed {meeting_id}: {exc}")
                continue
            emitted += 1
            yield self._record("GetMeeting", response, params)
            if index == 1 or index % 25 == 0 or index == total:
                print(f"  ModernGov XML meetings {index}/{total}")
