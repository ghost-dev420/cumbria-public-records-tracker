from __future__ import annotations

from collections.abc import Iterator

import httpx

from ..browser import BrowserHttpClient
from ..models import Record
from .lgsco_search import LgscoSearchCollector


class LgscoBrowserCollector(LgscoSearchCollector):
    """LGSCO collector using transparent headless Chromium.

    Some LGSCO listing/search pages return a content-stripped shell to plain
    HTTP clients while rendering the public result set in a normal browser.
    Reuse the normal bounded LGSCO search/fallback logic, changing only the
    transport. No stealth plugins, CAPTCHA solving, proxy rotation, or access-
    control bypass behaviour is used.
    """

    def _get(self, url: str):
        # BrowserHttpClient controls its own browser request headers/session.
        return self.client.get(url)

    def collect(self) -> Iterator[Record]:
        # Before launching Chromium, prove that the host is reachable with the
        # normal bounded HTTP client.  On Android/proot a transient DNS failure
        # can otherwise leave Playwright waiting in browser/page cleanup long
        # after navigation has already failed.  HTTP status failures (403 etc.)
        # are deliberately allowed through because the browser transport may be
        # required precisely when the plain HTTP endpoint rejects/strips a
        # non-browser response.
        previous_client = self.client
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
        timeout = float(self.source.get("browser_timeout", 20.0))
        with BrowserHttpClient(delay=delay, timeout=timeout) as browser:
            self.client = browser  # type: ignore[assignment]
            try:
                yield from super().collect()
            finally:
                self.client = previous_client
