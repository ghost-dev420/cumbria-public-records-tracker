from __future__ import annotations

import time
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx


class SafeHttpClient:
    def __init__(
        self,
        *,
        delay: float = 0.75,
        timeout: float = 30.0,
        respect_robots: bool = True,
    ) -> None:
        self.delay = delay
        self.respect_robots = respect_robots
        self.robot_user_agent = "CumbriaPublicRecordsTracker"
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self.user_agent = (
            "CumbriaPublicRecordsTracker/0.2 "
            "(+https://github.com/ghost-dev420/cumbria-public-records-tracker)"
        )
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/json,application/pdf,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.8",
            },
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "SafeHttpClient":
        return self

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
            self._wait(parts.netloc.casefold())
            response = self.client.get(robots_url)
            self._last_request[parts.netloc.casefold()] = time.monotonic()
            if response.status_code == 200:
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(response.text.splitlines())
        except httpx.HTTPError:
            parser = None
        self._robots[origin] = parser
        return parser

    def get(self, url: str, **kwargs: object) -> httpx.Response:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"}:
            raise ValueError(f"Unsupported URL scheme: {scheme}")
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
        response = self.client.get(url, **kwargs)
        self._last_request[host] = time.monotonic()
        response.raise_for_status()
        return response
