from __future__ import annotations

import re
from collections import deque
from urllib.parse import urljoin, urlsplit

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

    def collect(self):
        evidence = EvidenceClass(self.source["evidence_class"])
        link_re = re.compile(self.source.get("include_link_regex", ".*"), re.I)
        filter_text = self.source.get("filter_text", "").casefold()
        pagination_text = self.source.get("pagination_text", "next").casefold()
        max_records = int(self.source.get("max_records", 100))
        queue = deque(self.source.get("start_urls", []))
        seen_pages: set[str] = set()
        seen_records: set[str] = set()
        pages = 0
        emitted = 0

        while queue and pages < self.page_limit and emitted < max_records:
            page_url = queue.popleft()
            if page_url in seen_pages:
                continue
            seen_pages.add(page_url)
            try:
                response = self.client.get(page_url)
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
                href = urljoin(str(response.url), anchor_tag["href"])
                anchor = " ".join(anchor_tag.stripped_strings).strip()
                context = " ".join(anchor_tag.parent.stripped_strings) if anchor_tag.parent else anchor
                if self.source.get("follow_pagination", False) and pagination_text in anchor.casefold():
                    if urlsplit(href).netloc.casefold() == base_host and href not in seen_pages:
                        queue.append(href)
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
                try:
                    item = self.client.get(href)
                except Exception as exc:
                    self.errors.append(exc)
                    print(f"WARN attachment fetch failed {href}: {exc}")
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
