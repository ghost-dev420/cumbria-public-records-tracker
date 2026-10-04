from datetime import date

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
        expected_start = f"https://example.test/listing?td={date.today().isoformat()}"
        if url == expected_start:
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


def test_dynamic_date_fragments_and_terminal_pagination_404():
    client = _Client()
    collector = HtmlListingCollector(
        {
            "id": "lgsco_test",
            "name": "LGSCO test",
            "kind": "html_listing",
            "evidence_class": "REGULATORY_FINDING",
            "start_urls": ["https://example.test/listing?td={today}#cookie"],
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
    assert "{today}" not in client.calls[0]
    assert client.calls[0].endswith(date.today().isoformat())
    assert client.calls[-1] == "https://example.test/listing?page=2"


def test_unpadded_date_expansion_and_ignored_dead_record():
    today = date.today()
    unpadded = f"{today.year}-{today.month}-{today.day}"

    class _TombstoneClient:
        def __init__(self):
            self.calls: list[str] = []

        def get(self, url: str):
            self.calls.append(url)
            if url == f"https://example.test/listing?td={unpadded}":
                return _Response(
                    url,
                    b"""<html><body>
                    <a href='/decisions/children/care/23-010-393'>Dead case</a>
                    </body></html>""",
                )
            if url.endswith("/decisions/children/care/23-010-393"):
                request = httpx.Request("GET", url)
                response = httpx.Response(404, request=request)
                raise httpx.HTTPStatusError("not found", request=request, response=response)
            raise AssertionError(f"unexpected request {url}")

    client = _TombstoneClient()
    collector = HtmlListingCollector(
        {
            "id": "lgsco_test",
            "name": "LGSCO test",
            "kind": "html_listing",
            "evidence_class": "REGULATORY_FINDING",
            "start_urls": ["https://example.test/listing?td={today_unpadded}"],
            "include_link_regex": r"/decisions/.+/\d{2}-\d{3}-\d{3}/?$",
            "ignore_attachment_statuses": [404, 410],
            "page_limit": 2,
            "max_records": 10,
        },
        client,
    )

    records = list(collector.collect())

    assert len(records) == 1
    assert collector.errors == []
    assert client.calls[0].endswith(unpadded)
    assert "{today_unpadded}" not in client.calls[0]
