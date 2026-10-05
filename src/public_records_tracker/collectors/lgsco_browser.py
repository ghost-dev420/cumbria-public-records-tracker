from __future__ import annotations

from collections.abc import Iterator

import httpx

from ..browser import BrowserHttpClient
from ..models import EvidenceClass, Record
from .lgsco_search import LgscoSearchCollector


class LgscoBrowserCollector(LgscoSearchCollector):
    """LGSCO collector using transparent headless Chromium.

    Some LGSCO listing/search pages return a content-stripped shell to plain
    HTTP clients while rendering the public result set in a normal browser.
    Reuse the normal bounded LGSCO search/fallback logic, changing only the
    transport. No stealth plugins, CAPTCHA solving, proxy rotation, or access-
    control bypass behaviour is used.

    The council-performance listings are different: they are ordinary server-
    rendered pages and are more reliable through the normal bounded HTTP client.
    Keep that transport available as a fallback instead of routing every request
    through Chromium.
    """

    _plain_client = None

    def _get(self, url: str):
        if isinstance(self.client, BrowserHttpClient):
            # BrowserHttpClient controls its own browser request headers/session.
            return self.client.get(url)
        return super()._get(url)

    def _performance_fallback(self, evidence: EvidenceClass):
        """Fetch official performance listings with plain HTTP when available."""
        plain_client = self._plain_client
        if plain_client is None:
            yield from super()._performance_fallback(evidence)
            return

        browser_client = self.client
        self.client = plain_client
        try:
            yield from super()._performance_fallback(evidence)
        finally:
            self.client = browser_client

    def collect(self) -> Iterator[Record]:
        # Before launching Chromium, prove that the host is reachable with the
        # normal bounded HTTP client. On Android/proot a transient DNS failure
        # can otherwise leave Playwright waiting in browser/page cleanup long
        # after navigation has already failed. HTTP status failures (403 etc.)
        # are deliberately allowed through because the browser transport may be
        # required precisely when the plain HTTP endpoint rejects/strips a
        # non-browser response.
        previous_client = self.client
        self._plain_client = previous_client
        prime_url = str(self.source.get("prime_url") or "https://www.lgo.org.uk/decisions")
        try:
            previous_client.get(prime_url, headers=self._headers())
        except httpx.RequestError as exc:
            self.errors.append(exc)
            print(
                f"WARN LGSCO browser preflight failed {prime_url}; "
                f"skipping browser collection for this run: {exc}"
            )
            return
        except httpx.HTTPStatusError:
            pass

        delay = float(self.source.get("browser_delay", 0.75))
        # Keep a hard ceiling even if an older config still requests 45s. The
        # browser is a fallback transport and must never hold the whole
        # production collector hostage for long network/DNS failure paths.
        timeout = min(float(self.source.get("browser_timeout", 20.0)), 20.0)
        with BrowserHttpClient(delay=delay, timeout=timeout) as browser:
            self.client = browser  # type: ignore[assignment]
            try:
                yield from super().collect()
            finally:
                self.client = previous_client
                self._plain_client = None
