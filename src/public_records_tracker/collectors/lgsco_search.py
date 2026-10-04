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


class LgscoSearchCollector:
    """Collect LGSCO search-result pages and any discoverable decision pages.

    LGSCO's result cards are not consistently exposed as normal anchor tags to
    automated clients. This collector therefore pages SearchResults numerically,
    scans the raw response for decision URLs, and always keeps the result page
    itself so the LGSCO extractor can fall back to the structured text shown in
    each result card.
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
        # LGSCO's SearchResults endpoint is known to accept this unpadded form.
        return f"{today.year}-{today.month}-{today.day}"

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

    def collect(self):
        evidence = EvidenceClass(self.source["evidence_class"])
        seen_decisions: set[str] = set()
        ignored_statuses = {
            int(value) for value in self.source.get("ignore_decision_statuses", [404, 410])
        }

        active_to_date = str(self.source.get("to_date") or self._upper_date())
        fallback_to_date = self.source.get("fallback_to_date")

        for page in range(1, self.page_limit + 1):
            url = self._search_url(page, to_date=active_to_date)
            try:
                response = self.client.get(url)
            except Exception as exc:
                self.errors.append(exc)
                print(f"WARN LGSCO search fetch failed {url}: {exc}")
                break

            soup = BeautifulSoup(response.content, "html.parser")
            text = soup.get_text("\n", strip=True)
            references = {
                match.group(0).replace(" ", "-") for match in _CASE_REF.finditer(text)
            }
            decision_urls = self._decision_urls(str(response.url), response.content)

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
                    response = self.client.get(fallback_url)
                except Exception as exc:
                    self.errors.append(exc)
                    print(f"WARN LGSCO baseline fetch failed {fallback_url}: {exc}")
                    break
                soup = BeautifulSoup(response.content, "html.parser")
                text = soup.get_text("\n", strip=True)
                references = {
                    match.group(0).replace(" ", "-")
                    for match in _CASE_REF.finditer(text)
                }
                decision_urls = self._decision_urls(
                    str(response.url), response.content
                )

            print(
                f"LGSCO {self.source['id']}: page {page}/{self.page_limit}, "
                f"{len(references)} case references, {len(decision_urls)} decision URLs"
            )

            yield Record(
                source_id=self.source["id"],
                url=str(response.url),
                title=f"LGSCO search results: {self.source['organisation_name']} page {page}",
                body=response.content,
                content_type=response.headers.get("content-type", "text/html").split(";", 1)[0],
                evidence_class=evidence,
                status_code=response.status_code,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                metadata={
                    "listing": True,
                    "lgsco_search": True,
                    "page": page,
                    "parse_listing_results": not bool(decision_urls),
                },
            )

            if not references and not decision_urls:
                break

            for decision_url in decision_urls:
                if decision_url in seen_decisions:
                    continue
                seen_decisions.add(decision_url)
                try:
                    item = self.client.get(decision_url)
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
