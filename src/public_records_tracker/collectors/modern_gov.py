from __future__ import annotations

import re
from collections import deque
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..archive import canonicalize_url
from ..http import SafeHttpClient
from ..models import EvidenceClass, Record


DEFAULT_ALLOWED_PATH_REGEX = (
    r"/(?:mgMemberIndex|mgUserInfo|mgCommitteeDetails|mgListCommittees|"
    r"ieListMeetings|ieListDocuments|ceListDocuments|mgMeetingAttendance|"
    r"mgDeclarationSubmission|mgOutsideBodyDetails)\.aspx$"
)


class ModernGovCollector:
    """Crawl a bounded ModernGov graph while preserving every fetched HTML page."""

    def __init__(
        self,
        source: dict,
        client: SafeHttpClient,
        *,
        page_limit: int | None = None,
    ) -> None:
        self.source = source
        self.client = client
        self.page_limit = page_limit or int(source.get("page_limit", 200))
        self.max_depth = int(source.get("max_depth", 3))
        self.allowed_path = re.compile(
            source.get("allowed_path_regex", DEFAULT_ALLOWED_PATH_REGEX),
            re.IGNORECASE,
        )
        self.errors: list[Exception] = []

    def collect(self):
        evidence = EvidenceClass(self.source["evidence_class"])
        queue = deque((url, 0) for url in self.source.get("start_urls", []))
        seen: set[str] = set()
        pages = 0

        while queue and pages < self.page_limit:
            page_url, depth = queue.popleft()
            canonical = canonicalize_url(page_url)
            if canonical in seen:
                continue
            seen.add(canonical)

            try:
                response = self.client.get(page_url)
            except Exception as exc:
                self.errors.append(exc)
                print(f"WARN ModernGov fetch failed {page_url}: {exc}")
                continue
            pages += 1
            content_type = response.headers.get("content-type", "text/html").split(";", 1)[0]
            if "html" not in content_type.casefold():
                continue

            soup = BeautifulSoup(response.content, "html.parser")
            heading = soup.find(["h1", "h2"])
            title = " ".join(heading.stripped_strings) if heading else str(response.url)
            yield Record(
                source_id=self.source["id"],
                url=str(response.url),
                title=title,
                body=response.content,
                content_type=content_type,
                evidence_class=evidence,
                status_code=response.status_code,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                metadata={"modern_gov": True, "crawl_depth": depth},
            )

            if depth >= self.max_depth:
                continue

            base_host = urlsplit(str(response.url)).netloc.casefold()
            for anchor in soup.find_all("a", href=True):
                href = urljoin(str(response.url), anchor["href"])
                parts = urlsplit(href)
                if parts.netloc.casefold() != base_host:
                    continue
                if not self.allowed_path.search(parts.path):
                    continue
                if "mgdeclarationsubmissionprintview.aspx" in parts.path.casefold():
                    continue
                candidate = canonicalize_url(href)
                if candidate not in seen:
                    queue.append((href, depth + 1))
