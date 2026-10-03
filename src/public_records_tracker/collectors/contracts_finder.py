from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

from ..http import SafeHttpClient
from ..models import EvidenceClass, Record


class PaginationLimitReached(RuntimeError):
    pass


class ContractsFinderCollector:
    def __init__(self, source: dict, client: SafeHttpClient, *, page_limit: int | None = None) -> None:
        self.source = source
        self.client = client
        self.page_limit = page_limit or int(source.get("page_limit", 20))
        self.errors: list[Exception] = []

    @staticmethod
    def _buyer_names(release: dict) -> list[str]:
        names: list[str] = []
        buyer = release.get("buyer") or {}
        if buyer.get("name"):
            names.append(str(buyer["name"]))
        for party in release.get("parties") or []:
            roles = {str(x).casefold() for x in party.get("roles") or []}
            if "buyer" in roles and party.get("name"):
                names.append(str(party["name"]))
        return names

    def collect(self):
        evidence = EvidenceClass(self.source["evidence_class"])
        days_back = int(self.source.get("days_back", 14))
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days_back)
        endpoint = self.source["endpoint"]
        stages = str(self.source.get("stages", "tender,award")).strip()
        params: dict[str, object] = {
            "publishedFrom": start.isoformat(),
            "publishedTo": end.isoformat(),
            "limit": 100,
        }
        if stages:
            params["stages"] = stages
        terms = [str(x).casefold() for x in self.source.get("buyer_terms", [])]

        for page_no in range(self.page_limit):
            response = self.client.get(endpoint, params=params)
            package = response.json()
            yield Record(
                source_id=self.source["id"], url=str(response.url),
                title=f"Contracts Finder OCDS search page {page_no + 1}",
                body=response.content, content_type="application/json", evidence_class=evidence,
                status_code=response.status_code, etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                metadata={"listing": True, "page": page_no + 1, "stages": stages},
            )
            for release in package.get("releases") or []:
                buyers = self._buyer_names(release)
                if terms and not any(term in name.casefold() for term in terms for name in buyers):
                    continue
                tender = release.get("tender") or {}
                title = tender.get("title") or release.get("ocid") or release.get("id") or "Contract notice"
                record_url = release.get("uri") or release.get("url") or str(response.url)
                yield Record(
                    source_id=self.source["id"], url=str(record_url), title=str(title),
                    body=(json.dumps(release, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode(),
                    content_type="application/json", evidence_class=evidence,
                    published_at=release.get("date") or package.get("publishedDate"),
                    metadata={
                        "buyers": buyers,
                        "ocid": release.get("ocid"),
                        "release_id": release.get("id"),
                        "source_system": "Contracts Finder OCDS",
                    },
                )
            links = package.get("links") or {}
            next_link = links.get("next") if isinstance(links, dict) else None
            cursor = package.get("cursor") or package.get("nextCursor")
            has_next = bool(next_link or cursor)
            if has_next and page_no + 1 >= self.page_limit:
                error = PaginationLimitReached(
                    f"Contracts Finder still had another page after configured page_limit={self.page_limit}"
                )
                self.errors.append(error)
                print(f"WARN {error}")
                break
            if next_link:
                endpoint = urljoin(endpoint, str(next_link))
                params = {}
            elif cursor:
                params["cursor"] = cursor
            else:
                break
