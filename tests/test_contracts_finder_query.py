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
