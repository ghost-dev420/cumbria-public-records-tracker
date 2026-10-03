from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urljoin

import httpx

from ..http import SafeHttpClient
from ..models import EvidenceClass, Record


class PaginationLimitReached(RuntimeError):
    pass


_RELEASE_COLUMN = re.compile(r"^releases/(\d+)/(.*)$", re.IGNORECASE)


def _decode_csv(body: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _daily_release_rows(body: bytes):
    """Yield the flattened OCDS release sections from a Contracts Finder daily CSV."""
    text = _decode_csv(body)
    if not text.strip():
        return
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        grouped: dict[int, dict[str, str]] = {}
        for raw_key, raw_value in row.items():
            key = str(raw_key or "").strip().replace("\\", "/")
            match = _RELEASE_COLUMN.match(key)
            if not match:
                continue
            release_index = int(match.group(1))
            field = match.group(2).casefold()
            value = str(raw_value or "").strip()
            grouped.setdefault(release_index, {})[field] = value
        for release_index in sorted(grouped):
            yield grouped[release_index]


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
        if isinstance(buyer, dict) and buyer.get("name"):
            names.append(str(buyer["name"]))
        for party in release.get("parties") or []:
            if not isinstance(party, dict):
                continue
            roles = {str(x).casefold() for x in party.get("roles") or []}
            if "buyer" in roles and party.get("name"):
                names.append(str(party["name"]))
        return names

    @staticmethod
    def _matches_buyer(names: list[str], terms: list[str]) -> bool:
        if not terms:
            return True
        return any(term in name.casefold() for term in terms for name in names)

    @staticmethod
    def _daily_url(template: str, day: date) -> str:
        return template.format(
            year=f"{day.year:04d}", month=f"{day.month:02d}", day=f"{day.day:02d}"
        )

    def _release_records(
        self,
        *,
        payload: dict,
        response_url: str,
        evidence: EvidenceClass,
        buyers_hint: list[str] | None = None,
        published_hint: str | None = None,
    ):
        releases = payload.get("releases")
        if not isinstance(releases, list):
            releases = [payload] if payload.get("ocid") or payload.get("awards") else []
        for release in releases:
            if not isinstance(release, dict):
                continue
            buyers = self._buyer_names(release) or list(buyers_hint or [])
            tender = release.get("tender") or {}
            title = (
                (tender.get("title") if isinstance(tender, dict) else None)
                or release.get("ocid")
                or release.get("id")
                or "Contract notice"
            )
            record_url = release.get("uri") or release.get("url") or response_url
            yield Record(
                source_id=self.source["id"],
                url=str(record_url),
                title=str(title),
                body=(json.dumps(release, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode(),
                content_type="application/json",
                evidence_class=evidence,
                published_at=release.get("date") or published_hint,
                metadata={
                    "buyers": buyers,
                    "ocid": release.get("ocid"),
                    "release_id": release.get("id"),
                    "source_system": "Contracts Finder OCDS",
                },
            )

    def _collect_daily_csv(
        self,
        *,
        evidence: EvidenceClass,
        start: datetime,
        end: datetime,
        terms: list[str],
    ):
        daily_template = str(self.source["daily_csv_endpoint"])
        release_template = str(
            self.source.get("release_endpoint")
            or "https://www.contractsfinder.service.gov.uk/Published/Notice/releases/{id}.json"
        )
        seen_release_ids: set[str] = set()
        current = start.date()
        final = end.date()

        while current <= final:
            daily_url = self._daily_url(daily_template, current)
            try:
                response = self.client.get(daily_url)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 404:
                    current += timedelta(days=1)
                    continue
                self.errors.append(exc)
                print(f"WARN Contracts Finder daily CSV {daily_url}: {exc}")
                if status in {403, 429}:
                    break
                current += timedelta(days=1)
                continue

            for flattened in _daily_release_rows(response.content):
                buyer_name = flattened.get("buyer/name", "").strip()
                buyers = [buyer_name] if buyer_name else []
                if not self._matches_buyer(buyers, terms):
                    continue
                release_id = flattened.get("id", "").strip()
                if not release_id or release_id in seen_release_ids:
                    continue
                seen_release_ids.add(release_id)
                release_url = release_template.format(id=quote(release_id, safe=""))
                try:
                    release_response = self.client.get(release_url)
                    payload = release_response.json()
                except (httpx.HTTPError, json.JSONDecodeError) as exc:
                    self.errors.append(exc)
                    print(f"WARN Contracts Finder release {release_id}: {exc}")
                    continue
                if not isinstance(payload, dict):
                    continue
                yield from self._release_records(
                    payload=payload,
                    response_url=str(release_response.url),
                    evidence=evidence,
                    buyers_hint=buyers,
                    published_hint=flattened.get("date") or None,
                )
            current += timedelta(days=1)

    def collect(self):
        evidence = EvidenceClass(self.source["evidence_class"])
        days_back = int(self.source.get("days_back", 14))
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days_back)
        terms = [str(x).casefold() for x in self.source.get("buyer_terms", [])]

        # Contracts Finder's public OCDS Search endpoint can filter by date and
        # procurement stage, but not buyer. Scanning that national cursor feed
        # and then filtering locally can exhaust hundreds of pages before one
        # local authority appears. When configured, use the official daily OCDS
        # CSV output as a bounded discovery index and fetch only matching release
        # JSON documents for evidence/extraction.
        if self.source.get("daily_csv_endpoint"):
            yield from self._collect_daily_csv(
                evidence=evidence,
                start=start,
                end=end,
                terms=terms,
            )
            return

        endpoint = self.source["endpoint"]
        stages = str(self.source.get("stages", "tender,award")).strip()
        params: dict[str, object] = {
            "publishedFrom": start.isoformat(),
            "publishedTo": end.isoformat(),
            "limit": 100,
        }
        if stages:
            params["stages"] = stages

        for page_no in range(self.page_limit):
            response = self.client.get(endpoint, params=params)
            package = response.json()
            yield Record(
                source_id=self.source["id"],
                url=str(response.url),
                title=f"Contracts Finder OCDS search page {page_no + 1}",
                body=response.content,
                content_type="application/json",
                evidence_class=evidence,
                status_code=response.status_code,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                metadata={"listing": True, "page": page_no + 1, "stages": stages},
            )
            for release in package.get("releases") or []:
                if not isinstance(release, dict):
                    continue
                buyers = self._buyer_names(release)
                if not self._matches_buyer(buyers, terms):
                    continue
                yield from self._release_records(
                    payload=release,
                    response_url=str(response.url),
                    evidence=evidence,
                    buyers_hint=buyers,
                    published_hint=package.get("publishedDate"),
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
