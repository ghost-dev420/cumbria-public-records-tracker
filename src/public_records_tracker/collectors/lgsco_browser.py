from __future__ import annotations

from collections.abc import Iterator

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
        delay = float(self.source.get("browser_delay", 0.75))
        timeout = float(self.source.get("browser_timeout", 45.0))
        with BrowserHttpClient(delay=delay, timeout=timeout) as browser:
            previous_client = self.client
            self.client = browser  # type: ignore[assignment]
            try:
                yield from super().collect()
            finally:
                self.client = previous_client
