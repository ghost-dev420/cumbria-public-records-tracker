from __future__ import annotations

from collections.abc import Iterator

from ..browser import BrowserHttpClient
from ..models import Record
from .modern_gov import ModernGovCollector


class ModernGovBrowserCollector(ModernGovCollector):
    """ModernGov crawler using plain headless Chromium instead of HTTPX.

    It inherits the same bounded same-host graph crawl and path allowlist as
    ModernGovCollector. Browser mode is intentionally transparent: no stealth,
    proxies, CAPTCHA solving, or access-control bypass logic is used.
    """

    def collect(self) -> Iterator[Record]:
        delay = float(self.source.get("browser_delay", 0.75))
        timeout = float(self.source.get("browser_timeout", 30.0))
        with BrowserHttpClient(delay=delay, timeout=timeout) as browser:
            previous_client = self.client
            self.client = browser  # type: ignore[assignment]
            try:
                yield from super().collect()
            finally:
                self.client = previous_client
