import json
from types import SimpleNamespace

from public_records_tracker.collectors.contracts_finder import ContractsFinderCollector
from public_records_tracker.collectors.find_tender import FindTenderCollector
from public_records_tracker.runner import _extraction_coverage_error


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


def _source(kind: str, *, page_limit: int = 2):
    return {
        "id": kind,
        "kind": kind,
        "evidence_class": "OFFICIAL_RECORD",
        "endpoint": "https://example.test/feed",
        "buyer_terms": ["Cumberland Council"],
        "page_limit": page_limit,
    }


def test_contracts_finder_uses_source_page_limit_and_reports_truncation():
    package = {"releases": [], "cursor": "more"}
    collector = ContractsFinderCollector(
        _source("contracts_finder", page_limit=2), FakeClient(package)
    )
    records = list(collector.collect())
    assert len(records) == 2
    assert collector.page_limit == 2
    assert len(collector.errors) == 1
    assert "page_limit=2" in str(collector.errors[0])


def test_find_tender_uses_source_page_limit_and_reports_truncation():
    package = {"releases": [], "nextCursor": "more"}
    collector = FindTenderCollector(
        _source("find_tender", page_limit=2), FakeClient(package)
    )
    records = list(collector.collect())
    assert len(records) == 2
    assert collector.page_limit == 2
    assert len(collector.errors) == 1
    assert "page_limit=2" in str(collector.errors[0])


def test_expected_structured_source_reports_zero_fact_coverage_failure():
    source = {
        "id": "spending",
        "expect_facts": True,
        "minimum_fact_count": 1,
    }
    error = _extraction_coverage_error(source, record_count=12, fact_count=0)
    assert error is not None
    assert "12 records" in str(error)


def test_expected_structured_source_reports_zero_record_coverage_failure():
    source = {
        "id": "lgsco",
        "expect_facts": True,
        "minimum_fact_count": 1,
    }
    error = _extraction_coverage_error(source, record_count=0, fact_count=0)
    assert error is not None
    assert "0 records" in str(error)


def test_archive_only_source_allows_zero_facts():
    source = {"id": "minutes", "expect_facts": False}
    assert _extraction_coverage_error(source, record_count=12, fact_count=0) is None
