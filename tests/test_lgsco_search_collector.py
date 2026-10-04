from urllib.parse import parse_qs, urlsplit

from public_records_tracker.collectors.lgsco_search import LgscoSearchCollector


class _Response:
    def __init__(self, url: str, body: bytes):
        self.url = url
        self.content = body
        self.status_code = 200
        self.headers = {"content-type": "text/html"}


class _Client:
    def __init__(self):
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs.get("headers")))
        query = parse_qs(urlsplit(url).query)
        page = int(query["page"][0])
        if page == 1:
            return _Response(
                url,
                b"""<html><body>
                <div>Westmorland and Furness Council (26 002 445)</div>
                <div>Statement Closed after initial enquiries Parking and other penalties 09-Aug-2026</div>
                <p>Summary: Example result.</p>
                </body></html>""",
            )
        if page == 2:
            return _Response(url, b"<html><body>No further results</body></html>")
        raise AssertionError(f"unexpected page {page}")


def test_pages_numerically_and_preserves_listing_for_fallback_extraction():
    client = _Client()
    collector = LgscoSearchCollector(
        {
            "id": "lgsco_westmorland_furness",
            "name": "LGSCO decisions concerning Westmorland and Furness Council",
            "kind": "lgsco_search",
            "evidence_class": "REGULATORY_FINDING",
            "organisation_name": "Westmorland and Furness Council",
            "page_limit": 10,
            "prime_search": False,
        },
        client,
    )

    records = list(collector.collect())

    assert len(records) == 1
    first = records[0]
    assert first.metadata["parse_listing_results"] is True
    first_query = parse_qs(urlsplit(client.calls[0][0]).query)
    assert first_query["aname"] == ["Westmorland and Furness Council"]
    assert first_query["dc"] == ["c+nu+u+"]
    assert first_query["fd"] == ["0001-01-01"]
    assert first_query["page"] == ["1"]
    assert first_query["sortOrder"] == ["descending"]
    assert client.calls[1][0].find("page=2") != -1
    headers = client.calls[0][1]
    assert headers is not None
    assert headers["User-Agent"].startswith("Mozilla/5.0")
    assert headers["Referer"].endswith("/decisions")
    assert collector.errors == []


class _FallbackClient:
    def __init__(self, performance_url: str):
        self.performance_url = performance_url
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs.get("headers")))
        if url == "https://www.lgo.org.uk/decisions":
            return _Response(url, b"<html><body>Decision search</body></html>")
        if "/Decisions/SearchResults" in url:
            return _Response(url, b"<html><body>Search shell only</body></html>")
        if url == self.performance_url:
            return _Response(
                url,
                b"""<html><body>
                <h1>Decisions for Westmorland and Furness Council</h1>
                <div>Westmorland and Furness Council (24 019 797)</div>
                <div>Statement Upheld Other 06-Nov-2025</div>
                <p>Summary: The Council agreed to apologise and make service improvements.</p>
                </body></html>""",
            )
        raise AssertionError(f"unexpected URL {url}")


def test_empty_search_uses_official_performance_fallback():
    performance_url = "https://www.lgo.org.uk/performance/fallback"
    client = _FallbackClient(performance_url)
    collector = LgscoSearchCollector(
        {
            "id": "lgsco_westmorland_furness",
            "name": "LGSCO decisions concerning Westmorland and Furness Council",
            "kind": "lgsco_search",
            "evidence_class": "REGULATORY_FINDING",
            "organisation_name": "Westmorland and Furness Council",
            "fallback_to_date": "2026-9-7",
            "performance_fallback_urls": [performance_url],
        },
        client,
    )

    records = list(collector.collect())

    assert len(records) == 1
    record = records[0]
    assert record.metadata["lgsco_performance_fallback"] is True
    assert record.metadata["parse_listing_results"] is True
    assert b"24 019 797" in record.body
    assert client.calls[0][0] == "https://www.lgo.org.uk/decisions"
    search_calls = [url for url, _ in client.calls if "/Decisions/SearchResults" in url]
    assert len(search_calls) == 2
    assert any("td=2026-9-7" in url for url in search_calls)
    assert client.calls[-1][0] == performance_url
    assert collector.errors == []
