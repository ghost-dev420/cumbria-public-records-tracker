from public_records_tracker.collectors.lgsco_browser import LgscoBrowserCollector
from public_records_tracker.models import EvidenceClass


class _Response:
    def __init__(self, url: str, body: bytes):
        self.url = url
        self.content = body
        self.status_code = 200
        self.headers = {"content-type": "text/html"}


class _PlainClient:
    def __init__(self, performance_url: str):
        self.performance_url = performance_url
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs.get("headers")))
        assert url == self.performance_url
        return _Response(
            url,
            b"""<html><body>
            <h1>Decisions for Westmorland and Furness Council</h1>
            <div>Westmorland and Furness Council (24 019 797)</div>
            <div>Statement Upheld Other 06-Nov-2025</div>
            <p>Summary: The Council agreed to make service improvements.</p>
            </body></html>""",
        )


class _BrowserSentinel:
    pass


def test_browser_collector_uses_plain_http_for_performance_fallback():
    performance_url = "https://www.lgo.org.uk/your-councils-performance/example"
    plain = _PlainClient(performance_url)
    browser = _BrowserSentinel()
    collector = LgscoBrowserCollector(
        {
            "id": "lgsco_westmorland_furness",
            "name": "LGSCO decisions concerning Westmorland and Furness Council",
            "kind": "lgsco_browser",
            "evidence_class": "REGULATORY_FINDING",
            "organisation_name": "Westmorland and Furness Council",
            "performance_fallback_urls": [performance_url],
        },
        plain,
    )
    collector._plain_client = plain
    collector.client = browser  # type: ignore[assignment]

    records = list(collector._performance_fallback(EvidenceClass.REGULATORY_FINDING))

    assert len(records) == 1
    assert records[0].metadata["lgsco_performance_fallback"] is True
    assert b"24 019 797" in records[0].body
    assert plain.calls[0][0] == performance_url
    assert plain.calls[0][1] is not None
    assert plain.calls[0][1]["User-Agent"].startswith("Mozilla/5.0")
    assert collector.client is browser
