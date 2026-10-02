from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx


@dataclass(slots=True)
class BrowserResponse:
    url: str
    status_code: int
    content: bytes
    headers: dict[str, str]


class BrowserHttpClient:
    """Small, transparent Playwright client for public pages.

    This intentionally does not use stealth plugins, proxy rotation, CAPTCHA
    solving, webdriver masking, or other access-control circumvention.
    """

    def __init__(
        self,
        *,
        delay: float = 0.75,
        timeout: float = 30.0,
        respect_robots: bool = True,
    ) -> None:
        self.delay = delay
        self.timeout_ms = int(timeout * 1000)
        self.respect_robots = respect_robots
        self.robot_user_agent = "CumbriaPublicRecordsTracker"
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._robots_client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "CumbriaPublicRecordsTracker/0.2 "
                    "(+https://github.com/ghost-dev420/cumbria-public-records-tracker)"
                )
            },
        )

    def __enter__(self) -> "BrowserHttpClient":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - environment-specific
            raise RuntimeError(
                "Browser collection requires the 'browser' optional dependency"
            ) from exc

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._context = self._browser.new_context(
            locale="en-GB",
            extra_http_headers={
                "X-Public-Records-Tracker": (
                    "https://github.com/ghost-dev420/cumbria-public-records-tracker"
                )
            },
        )
        self._page = self._context.new_page()
        return self

    def close(self) -> None:
        if self._page is not None:
            self._page.close()
            self._page = None
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self._robots_client.close()

    def __exit__(self, *args: object) -> None:
        self.close()

    @staticmethod
    def _origin(url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), "", "", ""))

    def _wait(self, host: str, minimum_delay: float | None = None) -> None:
        delay = max(self.delay, minimum_delay or 0.0)
        wait = delay - (time.monotonic() - self._last_request.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)

    def _load_robots(self, url: str) -> RobotFileParser | None:
        origin = self._origin(url)
        if origin in self._robots:
            return self._robots[origin]
        parts = urlsplit(origin)
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        parser: RobotFileParser | None = None
        try:
            response = self._robots_client.get(robots_url)
            if response.status_code == 200:
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(response.text.splitlines())
        except httpx.HTTPError:
            parser = None
        self._robots[origin] = parser
        return parser

    def get(self, url: str) -> BrowserResponse:
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"}:
            raise ValueError(f"Unsupported URL scheme: {parts.scheme}")
        if self._page is None:
            raise RuntimeError("BrowserHttpClient must be used as a context manager")

        host = parts.netloc.casefold()
        crawl_delay: float | None = None
        if self.respect_robots and parts.path != "/robots.txt":
            parser = self._load_robots(url)
            if parser is not None:
                if not parser.can_fetch(self.robot_user_agent, url):
                    raise PermissionError(f"robots.txt disallows collection of {url}")
                configured = parser.crawl_delay(self.robot_user_agent)
                if configured is None:
                    configured = parser.crawl_delay("*")
                if configured is not None:
                    crawl_delay = float(configured)

        self._wait(host, crawl_delay)
        response = self._page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=self.timeout_ms,
        )
        self._last_request[host] = time.monotonic()
        if response is None:
            raise RuntimeError(f"Browser navigation returned no response for {url}")

        status = response.status
        final_url = self._page.url
        if status >= 400:
            request = httpx.Request("GET", final_url)
            http_response = httpx.Response(status, request=request)
            raise httpx.HTTPStatusError(
                f"Browser navigation returned HTTP {status} for {final_url}",
                request=request,
                response=http_response,
            )

        # Give simple client-side rendering a moment without waiting for every
        # analytics/network request to become idle.
        self._page.wait_for_timeout(250)
        html = self._page.content().encode("utf-8")
        headers = {str(k).lower(): str(v) for k, v in response.all_headers().items()}
        headers.setdefault("content-type", "text/html; charset=utf-8")
        return BrowserResponse(
            url=final_url,
            status_code=status,
            content=html,
            headers=headers,
        )
