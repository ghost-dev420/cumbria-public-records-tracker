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
        self.calls: list[str] = []

    def get(self, url: str):
        self.calls.append(url)
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
        },
        client,
    )

    records = list(collector.collect())

    assert len(records) == 2
    first = records[0]
    assert first.metadata["parse_listing_results"] is True
    first_query = parse_qs(urlsplit(client.calls[0]).query)
    assert first_query["aname"] == ["Westmorland and Furness Council"]
    assert first_query["dc"] == ["c+nu+u+"]
    assert first_query["fd"] == ["0001-01-01"]
    assert first_query["page"] == ["1"]
    assert first_query["sortOrder"] == ["descending"]
    assert client.calls[1].find("page=2") != -1
    assert collector.errors == []
