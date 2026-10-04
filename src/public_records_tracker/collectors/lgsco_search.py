from __future__ import annotations

import html
import re
from datetime import date
from urllib.parse import urlencode, urljoin

import httpx
from bs4 import BeautifulSoup

from ..http import SafeHttpClient
from ..models import EvidenceClass, Record


_CASE_REF = re.compile(r"\b\d{2}[ -]\d{3}[ -]\d{3}\b")
_DECISION_PATH = re.compile(
    r"(?i)(/decisions/[a-z0-9%_'().,+& -]+/[a-z0-9%_'().,+& -]+/\d{2}-\d{3}-\d{3}/?)"
)
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.lgo.org.uk/decisions",
}


class LgscoSearchCollector:
    """Collect LGSCO decisions without depending on fragile result anchors.

    The public SearchResults endpoint can serve a search shell to non-browser
    clients even when the same URL contains results in a normal browser. We
    therefore establish a browser-like session, try the rolling search, retry a
    known-good historical upper date when configured, then fall back to official
    council-performance decision listings. Result-card text is retained for the
    LGSCO extractor, while discoverable decision pages are fetched as richer
    evidence when available.
    """

    def __init__(
        self,
        source: dict,
        client: SafeHttpClient,
        *,
        page_limit: int | None = None,
    ) -> None:
        self.source = source
        self.client = client
        self.page_limit = page_limit or int(source.get("page_limit", 20))
        self.errors: list[Exception] = []

    @staticmethod
    def _upper_date() -> str:
        today = date.today()
        return f"{today.year}-{today.month}-{today.day}"

    def _headers(self) -> dict[str, str]:
        headers = dict(_BROWSER_HEADERS)
        headers.update(
            {str(key): str(value) for key, value in self.source.get("request_headers", {}).items()}
        )
        return headers

    def _get(self, url: str):
        return self.client.get(url, headers=self._headers())

    def _prime_session(self) -> None:
        if not bool(self.source.get("prime_search", True)):
            return
        prime_url = str(self.source.get("prime_url") or "https://www.lgo.org.uk/decisions")
        try:
            self._get(prime_url)
        except Exception as exc:
            # Priming is an optimisation/workaround, not an evidence source. Do
            # not poison source health if SearchResults or performance fallback
            # still succeeds afterwards.
            print(f"WARN LGSCO session prime failed {prime_url}: {exc}")

    def _search_url(self, page: int, *, to_date: str | None = None) -> str:
        endpoint = str(
            self.source.get("endpoint")
            or "https://www.lgo.org.uk/Decisions/SearchResults"
        )
        params = {
            "aname": str(self.source["organisation_name"]),
            "dc": str(self.source.get("decision_codes", "c+nu+u+")),
            "fd": str(self.source.get("from_date", "0001-01-01")),
            "page": str(page),
            "sortOrder": str(self.source.get("sort_order", "descending")),
            "td": str(to_date or self.source.get("to_date") or self._upper_date()),
        }
        return f"{endpoint}?{urlencode(params)}"

    @staticmethod
    def _scan(response) -> tuple[set[str], list[str]]:
        soup = BeautifulSoup(response.content, "html.parser")
        text = soup.get_text("\n", strip=True)
        references = {
            match.group(0).replace(" ", "-") for match in _CASE_REF.finditer(text)
        }
        decision_urls = LgscoSearchCollector._decision_urls(
            str(response.url), response.content
        )
        return references, decision_urls

    @staticmethod
    def _decision_urls(base_url: str, body: bytes) -> list[str]:
        raw = html.unescape(body.decode("utf-8", errors="ignore")).replace("\\/", "/")
        found: list[str] = []
        seen: set[str] = set()
        for match in _DECISION_PATH.finditer(raw):
            url = urljoin(base_url, match.group(1).replace(" ", "%20"))
            if url not in seen:
                seen.add(url)
                found.append(url)
        return found

    def _listing_record(
        self,
        *,
        response,
        evidence: EvidenceClass,
        title: str,
        metadata: dict,
    ) -> Record:
        return Record(
            source_id=self.source["id"],
            url=str(response.url),
            title=title,
            body=response.content,
            content_type=response.headers.get("content-type", "text/html").split(";", 1)[0],
            evidence_class=evidence,
            status_code=response.status_code,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            metadata=metadata,
        )

    def _performance_fallback(self, evidence: EvidenceClass):
        urls = [str(url) for url in self.source.get("performance_fallback_urls", [])]
        if not urls:
            return
        print(
            f"LGSCO {self.source['id']}: SearchResults remained empty; "
            f"using {len(urls)} official council-performance listing(s)"
        )
        for index, url in enumerate(urls, start=1):
            try:
                response = self._get(url)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {404, 410}:
                    print(
                        f"INFO unavailable LGSCO performance listing {url} "
                        f"(HTTP {exc.response.status_code})"
                    )
                    continue
                self.errors.append(exc)
                print(f"WARN LGSCO performance fetch failed {url}: {exc}")
                continue
            except Exception as exc:
                self.errors.append(exc)
                print(f"WARN LGSCO performance fetch failed {url}: {exc}")
                continue

            references, decision_urls = self._scan(response)
            print(
                f"LGSCO {self.source['id']}: performance listing {index}/{len(urls)}, "
                f"{len(references)} case references, {len(decision_urls)} decision URLs"
            )
            if not references and not decision_urls:
                continue
            yield self._listing_record(
                response=response,
                evidence=evidence,
                title=(
                    f"LGSCO council-performance decisions: "
                    f"{self.source['organisation_name']} listing {index}"
                ),
                metadata={
                    "listing": True,
                    "lgsco_performance_fallback": True,
                    "parse_listing_results": True,
                    "performance_listing": index,
                },
            )

    def collect(self):
        evidence = EvidenceClass(self.source["evidence_class"])
        seen_decisions: set[str] = set()
        ignored_statuses = {
            int(value) for value in self.source.get("ignore_decision_statuses", [404, 410])
        }

        self._prime_session()
        active_to_date = str(self.source.get("to_date") or self._upper_date())
        fallback_to_date = self.source.get("fallback_to_date")

        for page in range(1, self.page_limit + 1):
            url = self._search_url(page, to_date=active_to_date)
            try:
                response = self._get(url)
            except Exception as exc:
                self.errors.append(exc)
                print(f"WARN LGSCO search fetch failed {url}: {exc}")
                break

            references, decision_urls = self._scan(response)

            if (
                page == 1
                and not references
                and not decision_urls
                and fallback_to_date
                and str(fallback_to_date) != active_to_date
            ):
                active_to_date = str(fallback_to_date)
                fallback_url = self._search_url(page, to_date=active_to_date)
                print(
                    f"LGSCO {self.source['id']}: rolling search returned no cases; "
                    f"retrying known-good baseline td={active_to_date}"
                )
                try:
                    response = self._get(fallback_url)
                except Exception as exc:
                    self.errors.append(exc)
                    print(f"WARN LGSCO baseline fetch failed {fallback_url}: {exc}")
                    break
                references, decision_urls = self._scan(response)

            if page == 1 and not references and not decision_urls:
                yield from self._performance_fallback(evidence)
                return

            print(
                f"LGSCO {self.source['id']}: page {page}/{self.page_limit}, "
                f"{len(references)} case references, {len(decision_urls)} decision URLs"
            )
            if not references and not decision_urls:
                break

            yield self._listing_record(
                response=response,
                evidence=evidence,
                title=f"LGSCO search results: {self.source['organisation_name']} page {page}",
                metadata={
                    "listing": True,
                    "lgsco_search": True,
                    "page": page,
                    "parse_listing_results": not bool(decision_urls),
                },
            )

            for decision_url in decision_urls:
                if decision_url in seen_decisions:
                    continue
                seen_decisions.add(decision_url)
                try:
                    item = self._get(decision_url)
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if status in ignored_statuses:
                        print(
                            f"INFO skipping unavailable LGSCO decision {decision_url} "
                            f"(HTTP {status})"
                        )
                        continue
                    self.errors.append(exc)
                    print(f"WARN LGSCO decision fetch failed {decision_url}: {exc}")
                    continue
                except Exception as exc:
                    self.errors.append(exc)
                    print(f"WARN LGSCO decision fetch failed {decision_url}: {exc}")
                    continue

                yield Record(
                    source_id=self.source["id"],
                    url=str(item.url),
                    title=decision_url.rsplit("/", 1)[-1],
                    body=item.content,
                    content_type=item.headers.get("content-type", "text/html").split(";", 1)[0],
                    evidence_class=evidence,
                    status_code=item.status_code,
                    etag=item.headers.get("etag"),
                    last_modified=item.headers.get("last-modified"),
                    metadata={"discovered_from": str(response.url), "lgsco_decision": True},
                )
