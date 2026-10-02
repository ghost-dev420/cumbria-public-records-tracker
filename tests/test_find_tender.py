import json
from types import SimpleNamespace

from public_records_tracker.collectors.find_tender import FindTenderCollector
from public_records_tracker.config import load_sources


class FakeClient:
    def __init__(self, package):
        self.package = package
        self.calls = []

    def get(self, endpoint, params=None):
        self.calls.append((endpoint, dict(params or {})))
        content = json.dumps(self.package).encode()
        return SimpleNamespace(
            url=endpoint,
            content=content,
            status_code=200,
            headers={},
            json=lambda: self.package,
        )


def test_find_tender_filters_to_configured_buyers():
    package = {
        "releases": [
            {
                "ocid": "ocds-h6vhtk-000001",
                "id": "release-1",
                "date": "2026-10-01T10:00:00Z",
                "buyer": {"name": "Cumberland Council"},
                "tender": {"title": "Road works"},
            },
            {
                "ocid": "ocds-h6vhtk-000002",
                "id": "release-2",
                "buyer": {"name": "Unrelated Authority"},
                "tender": {"title": "Other works"},
            },
        ]
    }
    source = {
        "id": "find_a_tender",
        "evidence_class": "OFFICIAL_RECORD",
        "endpoint": "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages",
        "buyer_terms": ["Cumberland Council"],
        "days_back": 30,
    }
    client = FakeClient(package)
    records = list(FindTenderCollector(source, client, page_limit=1).collect())
    assert len(records) == 2  # package + one matching release
    assert records[1].title == "Road works"
    assert records[1].metadata["ocid"] == "ocds-h6vhtk-000001"
    assert "updatedFrom" in client.calls[0][1]
    assert "updatedTo" in client.calls[0][1]


def test_supplemental_source_file_is_loaded():
    sources = load_sources(__import__("pathlib").Path("config/sources.yml"))
    source = next(item for item in sources if item["id"] == "find_a_tender")
    assert source["kind"] == "find_tender"
    assert "contracts_finder" in source["extractors"]
