from public_records_tracker.collectors.modern_gov_xml import ModernGovXmlCollector


class FakeResponse:
    def __init__(self, url: str, content: bytes):
        self.url = url
        self.content = content
        self.status_code = 200
        self.headers = {"content-type": "text/xml; charset=utf-8"}


class FakeClient:
    def __init__(self):
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url: str, **kwargs):
        params = kwargs.get("params")
        self.calls.append((url, params))
        if url.endswith("/GetCalendarEvents"):
            return FakeResponse(
                url,
                b"""<?xml version='1.0'?>
                <events>
                  <event><meetingid>101</meetingid><meetingtitle>One</meetingtitle></event>
                  <event><meetingid>102</meetingid><meetingtitle>Two</meetingtitle></event>
                </events>""",
            )
        meeting_id = str(params["lMeetingId"])
        return FakeResponse(
            f"{url}?lMeetingId={meeting_id}",
            f"<meeting><meetingid>{meeting_id}</meetingid><meetingtitle>Meeting {meeting_id}</meetingtitle></meeting>".encode(),
        )


def test_calendar_collection_expands_meetings_and_preserves_request_params() -> None:
    client = FakeClient()
    source = {
        "id": "modern-api",
        "kind": "modern_gov_xml",
        "evidence_class": "OFFICIAL_RECORD",
        "api_base_url": "https://example.moderngov.co.uk",
        "api_operations": ["GetCalendarEvents"],
        "meeting_days_back": 30,
        "meeting_days_forward": 30,
        "expand_meetings": True,
        "page_limit": 10,
    }
    records = list(ModernGovXmlCollector(source, client).collect())

    assert len(records) == 3
    assert client.calls[0][0].endswith("/GetCalendarEvents")
    calendar_params = client.calls[0][1]
    assert calendar_params["bGlobalCalendar"] == "true"
    assert calendar_params["lUserId"] == 0
    assert "sDateStart" in calendar_params
    assert "sDateEnd" in calendar_params

    assert [call[1]["lMeetingId"] for call in client.calls[1:]] == ["101", "102"]
    assert records[1].metadata["operation"] == "GetMeeting"
    assert records[1].metadata["request_params"] == {"lMeetingId": "101"}
