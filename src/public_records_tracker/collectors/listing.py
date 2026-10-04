from __future__ import annotations

import re
from collections import deque
from datetime import date
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from ..http import SafeHttpClient
from ..models import EvidenceClass, Record


class HtmlListingCollector:
    def __init__(
        self,
        source: dict,
        client: SafeHttpClient,
        *,
        page_limit: int | None = None,
    ) -> None:
        self.source = source
        self.client = client
        self.page_limit = page_limit or int(source.get("page_limit", 1))
        self.errors: list[Exception] = []

    @staticmethod
    def _clean_url(url: str) -> str:
        # Cookie/privacy anchors and other fragments never change the resource
        # being fetched, but can otherwise defeat de-duplication and get copied
        # into pagination URLs.
        return urldefrag(str(url))[0]

    @staticmethod
    def _expand_url(url: str) -> str:
        # Some listing endpoints need an explicit moving upper date bound to
        # render results server-side. Keep that date dynamic in source config
        # rather than baking in a value that quietly goes stale. A few legacy
        # ASP.NET search surfaces are picky about zero-padded month/day values,
        # so support the exact unpadded shape they emit themselves too.
        today = date.today()
        return (
            str(url)
            .replace("{today}", today.isoformat())
            .replace("{today_unpadded}", f"{today.year}-{today.month}-{today.day}")
        )

    def collect(self):
        evidence = EvidenceClass(self.source["evidence_class"])
        link_re = re.compile(self.source.get("include_link_regex", ".*"), re.I)
        filter_text = self.source.get("filter_text", "").casefold()
        pagination_text = self.source.get("pagination_text", "next").casefold()
        max_records = int(self.source.get("max_records", 100))
        queue = deque(
            (self._clean_url(self._expand_url(url)), False)
            for url in self.source.get("start_urls", [])
        )
        seen_pages: set[str] = set()
        seen_records: set[str] = set()
        pages = 0
        emitted = 0
        failed_hosts: set[str] = set()
        pagination_404_ends = bool(self.source.get("pagination_404_ends", False))
        ignored_attachment_statuses = {
            int(status) for status in self.source.get("ignore_attachment_statuses", [])
        }

        while queue and pages < self.page_limit and emitted < max_records:
            page_url, from_pagination = queue.popleft()
            if page_url in seen_pages:
                continue
            seen_pages.add(page_url)
            try:
                response = self.client.get(page_url)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if from_pagination and pagination_404_ends and status in {404, 410}:
                    print(
                        f"INFO listing pagination exhausted at {page_url} "
                        f"(HTTP {status})"
                    )
                    continue
                self.errors.append(exc)
                print(f"WARN listing fetch failed {page_url}: {exc}")
                continue
            except Exception as exc:
                self.errors.append(exc)
                print(f"WARN listing fetch failed {page_url}: {exc}")
                continue
            pages += 1
            ctype = response.headers.get("content-type", "text/html").split(";", 1)[0]
            yield Record(
                source_id=self.source["id"],
                url=str(response.url),
                title=f"Listing: {self.source.get('name', self.source['id'])}",
                body=response.content,
                content_type=ctype,
                evidence_class=evidence,
                status_code=response.status_code,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                metadata={"listing": True},
            )
            soup = BeautifulSoup(response.content, "html.parser")
            base_host = urlsplit(str(response.url)).netloc.casefold()
            for anchor_tag in soup.find_all("a", href=True):
                href = self._clean_url(urljoin(str(response.url), anchor_tag["href"]))
                anchor = " ".join(anchor_tag.stripped_strings).strip()
                context = " ".join(anchor_tag.parent.stripped_strings) if anchor_tag.parent else anchor
                if self.source.get("follow_pagination", False) and pagination_text in anchor.casefold():
                    if urlsplit(href).netloc.casefold() == base_host and href not in seen_pages:
                        queue.append((href, True))
                if not link_re.search(href):
                    continue
                if filter_text and filter_text not in context.casefold() and filter_text not in anchor.casefold():
                    continue
                if href in seen_records:
                    continue
                if not self.source.get("allow_cross_domain", False):
                    if urlsplit(href).netloc.casefold() != base_host:
                        continue
                seen_records.add(href)
                attachment_host = urlsplit(href).netloc.casefold()
                if attachment_host in failed_hosts:
                    continue
                try:
                    item = self.client.get(href)
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if status in ignored_attachment_statuses:
                        print(
                            f"INFO skipping unavailable listing record {href} "
                            f"(HTTP {status})"
                        )
                        continue
                    self.errors.append(exc)
                    print(f"WARN attachment fetch failed {href}: {exc}")
                    continue
                except Exception as exc:
                    self.errors.append(exc)
                    print(f"WARN attachment fetch failed {href}: {exc}")
                    # DNS/connectivity failures are normally host-wide. SafeHttpClient
                    # has already exhausted its retries, so avoid hammering every
                    # remaining attachment on the same unavailable host.
                    error_text = str(exc).casefold()
                    if any(marker in error_text for marker in (
                        "name resolution", "nodename nor servname",
                        "temporary failure", "connecterror",
                    )):
                        failed_hosts.add(attachment_host)
                        print(f"WARN suppressing further attachment fetches for unavailable host {attachment_host}")
                    continue
                item_type = item.headers.get(
                    "content-type", "application/octet-stream"
                ).split(";", 1)[0]
                title = anchor or urlsplit(href).path.rsplit("/", 1)[-1] or href
                yield Record(
                    source_id=self.source["id"],
                    url=str(item.url),
                    title=title,
                    body=item.content,
                    content_type=item_type,
                    evidence_class=evidence,
                    status_code=item.status_code,
                    etag=item.headers.get("etag"),
                    last_modified=item.headers.get("last-modified"),
                    metadata={"discovered_from": str(response.url), "anchor_text": anchor},
                )
                emitted += 1
                if emitted >= max_records:
                    break
