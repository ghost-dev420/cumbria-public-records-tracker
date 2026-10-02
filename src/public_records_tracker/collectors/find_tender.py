from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

from ..http import SafeHttpClient
from ..models import EvidenceClass, Record


class FindTenderCollector:
    def __init__(self, source: dict, client: SafeHttpClient, *, page_limit: int | None = None) -> None:
        self.source = source
        self.client = client
        self.page_limit = page_limit or 20

    @staticmethod
    def _buyer_names(release: dict) -> list[str]:
        names: list[str] = []
        buyer = release.get("buyer") or {}
        if isinstance(buyer, dict) and buyer.get("name"):
            names.append(str(buyer["name"]))
        for party in release.get("parties") or []:
            if not isinstance(party, dict):
                continue
            roles = {str(role).casefold() for role in party.get("roles") or []}
            if "buyer" in roles and party.get("name"):
                names.append(str(party["name"]))
        return names

    def collect(self):
        evidence = EvidenceClass(self.source["evidence_class"])
        days_back = int(self.source.get("days_back", 30))
        end = datetime.now(timezone.utc).replace(microsecond=0)
        start = end - timedelta(days=days_back)
        endpoint = self.source["endpoint"]
        params: dict[str, object] = {
            "updatedFrom": start.replace(tzinfo=None).isoformat(),
            "updatedTo": end.replace(tzinfo=None).isoformat(),
            "limit": 100,
        }
        stages = self.source.get("stages")
        if stages:
            params["stages"] = ",".join(str(stage) for stage in stages)
        terms = [str(term).casefold() for term in self.source.get("buyer_terms", [])]

        for page_no in range(self.page_limit):
            response = self.client.get(endpoint, params=params)
            package = response.json()
            yield Record(
                source_id=self.source["id"],
                url=str(response.url),
                title=f"Find a Tender OCDS release package {page_no + 1}",
                body=response.content,
                content_type="application/json",
                evidence_class=evidence,
                status_code=response.status_code,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                metadata={"listing": True, "page": page_no + 1},
            )

            for release in package.get("releases") or []:
                if not isinstance(release, dict):
                    continue
                buyers = self._buyer_names(release)
                if terms and not any(
                    term in buyer_name.casefold() for term in terms for buyer_name in buyers
                ):
                    continue
                tender = release.get("tender") or {}
                title = (
                    (tender.get("title") if isinstance(tender, dict) else None)
                    or release.get("ocid")
                    or release.get("id")
                    or "Find a Tender notice"
                )
                record_url = release.get("uri") or release.get("url") or str(response.url)
                yield Record(
                    source_id=self.source["id"],
                    url=str(record_url),
                    title=str(title),
                    body=(
                        json.dumps(release, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
                    ).encode(),
                    content_type="application/json",
                    evidence_class=evidence,
                    published_at=release.get("date") or package.get("publishedDate"),
                    metadata={
                        "buyers": buyers,
                        "ocid": release.get("ocid"),
                        "release_id": release.get("id"),
                        "source_system": "Find a Tender OCDS",
                    },
                )

            links = package.get("links") or {}
            next_link = links.get("next") if isinstance(links, dict) else None
            cursor = package.get("cursor") or package.get("nextCursor")
            if next_link:
                endpoint = urljoin(endpoint, str(next_link))
                params = {}
            elif cursor:
                params["cursor"] = cursor
            else:
                break
