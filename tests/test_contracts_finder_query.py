import json

from public_records_tracker.collectors.contracts_finder import ContractsFinderCollector


class _Response:
    def __init__(self):
        self.url = "https://example.test/contracts?cursor=1"
        self.content = json.dumps({"releases": [], "links": {}}).encode()
        self.status_code = 200
        self.headers = {}

    def json(self):
        return {"releases": [], "links": {}}


class _Client:
    def __init__(self):
        self.calls = []

    def get(self, endpoint, params=None):
        self.calls.append((endpoint, dict(params or {})))
        return _Response()


class _DailyResponse:
    def __init__(self, *, url, content=b"", payload=None):
        self.url = url
        self.content = content
        self.status_code = 200
        self.headers = {}
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise AssertionError("json() should only be called for release responses")
        return self._payload


class _DailyClient:
    def __init__(self):
        self.calls = []

    def get(self, endpoint, params=None):
        self.calls.append((endpoint, dict(params or {})))
        if "/Harvester/Notices/Data/CSV/" in endpoint:
            content = (
                "releases/0/id,releases/0/buyer/name,releases/0/date\n"
                "notice-1,Cumberland Council,2026-10-01T12:00:00Z\n"
                "notice-2,Unrelated Council,2026-10-01T12:00:00Z\n"
            ).encode()
            return _DailyResponse(url=endpoint, content=content)
        if "/Published/Notice/releases/notice-1.json" in endpoint:
            return _DailyResponse(
                url=endpoint,
                payload={
                    "releases": [
                        {
                            "ocid": "ocds-test-1",
                            "id": "notice-1",
                            "date": "2026-10-01T12:00:00Z",
                            "buyer": {"name": "Cumberland Council"},
                            "tender": {"title": "Road maintenance"},
                            "awards": [
                                {
                                    "id": "award-1",
                                    "suppliers": [{"name": "Example Engineering Ltd"}],
                                }
                            ],
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request: {endpoint}")


def test_contracts_finder_defaults_to_tender_and_award_stages():
    client = _Client()
    collector = ContractsFinderCollector(
        {
            "id": "contracts_finder",
            "kind": "contracts_finder",
            "evidence_class": "OFFICIAL_RECORD",
            "endpoint": "https://example.test/contracts",
        },
        client,
        page_limit=1,
    )

    list(collector.collect())

    assert client.calls[0][1]["stages"] == "tender,award"
    assert client.calls[0][1]["limit"] == 100


def test_official_contracts_finder_uses_daily_csv_to_target_buyers(capsys):
    client = _DailyClient()
    collector = ContractsFinderCollector(
        {
            "id": "contracts_finder",
            "kind": "contracts_finder",
            "evidence_class": "OFFICIAL_RECORD",
            "endpoint": (
                "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"
            ),
            "buyer_terms": ["Cumberland Council"],
            "daily_days_back": 0,
        },
        client,
        page_limit=1,
    )

    records = list(collector.collect())
    output = capsys.readouterr().out

    assert len(records) == 1
    assert records[0].title == "Road maintenance"
    assert records[0].metadata["buyers"] == ["Cumberland Council"]
    assert len(client.calls) == 2
    assert "/Harvester/Notices/Data/CSV/" in client.calls[0][0]
    assert "/Published/Notice/releases/notice-1.json" in client.calls[1][0]
    assert all("/Published/Notices/OCDS/Search" not in call[0] for call in client.calls)
    assert "Contracts Finder: 1/1 days scanned, 1 matching releases, 1 fetched records" in output
