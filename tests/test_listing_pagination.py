import httpx

from public_records_tracker.collectors.listing import HtmlListingCollector


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
        if url == "https://example.test/listing":
            return _Response(
                url,
                b"""<html><body>
                <a href='/decisions/education/sen/25-000-276#analyticalCookies'>Case</a>
                <a href='/listing?page=2#analyticalCookies'>Next</a>
                </body></html>""",
            )
        if url == "https://example.test/decisions/education/sen/25-000-276":
            return _Response(url, b"<html><body>decision</body></html>")
        if url == "https://example.test/listing?page=2":
            request = httpx.Request("GET", url)
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)
        raise AssertionError(f"unexpected request {url}")


def test_fragments_are_removed_and_terminal_pagination_404_is_not_an_error():
    client = _Client()
    collector = HtmlListingCollector(
        {
            "id": "lgsco_test",
            "name": "LGSCO test",
            "kind": "html_listing",
            "evidence_class": "REGULATORY_FINDING",
            "start_urls": ["https://example.test/listing#cookie"],
            "include_link_regex": r"/decisions/.+/\d{2}-\d{3}-\d{3}/?$",
            "follow_pagination": True,
            "pagination_text": "next",
            "pagination_404_ends": True,
            "page_limit": 5,
            "max_records": 10,
        },
        client,
    )

    records = list(collector.collect())

    assert len(records) == 2
    assert records[1].url.endswith("/decisions/education/sen/25-000-276")
    assert collector.errors == []
    assert all("#" not in url for url in client.calls)
    assert client.calls[-1] == "https://example.test/listing?page=2"
