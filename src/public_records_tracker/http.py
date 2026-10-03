from __future__ import annotations

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx


_RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class SafeHttpClient:
    def __init__(
        self,
        *,
        delay: float = 0.75,
        timeout: float = 30.0,
        respect_robots: bool = True,
        retries: int = 3,
        retry_backoff: float = 1.0,
    ) -> None:
        self.delay = delay
        self.respect_robots = respect_robots
        self.retries = max(0, retries)
        self.retry_backoff = max(0.0, retry_backoff)
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

    def _retry_pause(self, response: httpx.Response | None, attempt: int) -> float:
        pause = self.retry_backoff * (2**attempt)
        if response is None:
            return pause
        retry_after = response.headers.get("retry-after")
        if not retry_after:
            return pause
        try:
            return max(pause, float(retry_after))
        except ValueError:
            try:
                when = parsedate_to_datetime(retry_after)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                seconds = (when - datetime.now(timezone.utc)).total_seconds()
                return max(pause, seconds, 0.0)
            except (TypeError, ValueError, OverflowError):
                return pause

    def _request_with_retries(self, url: str, host: str, **kwargs: object) -> httpx.Response:
        last_error: httpx.TransportError | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.client.get(url, **kwargs)
                self._last_request[host] = time.monotonic()
                if response.status_code in _RETRYABLE_STATUSES and attempt < self.retries:
                    time.sleep(self._retry_pause(response, attempt))
                    continue
                return response
            except httpx.TransportError as exc:
                last_error = exc
                self._last_request[host] = time.monotonic()
                if attempt >= self.retries:
                    raise
                time.sleep(self._retry_pause(None, attempt))
        assert last_error is not None
        raise last_error

    def _load_robots(self, url: str) -> RobotFileParser | None:
        origin = self._origin(url)
        if origin in self._robots:
            return self._robots[origin]
        parts = urlsplit(origin)
        host = parts.netloc.casefold()
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        self._wait(host)
        response = self._request_with_retries(robots_url, host)

        parser: RobotFileParser | None = None
        if response.status_code == 200:
            parser = RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(response.text.splitlines())
        elif response.status_code in {401, 403}:
            parser = RobotFileParser()
            parser.set_url(robots_url)
            parser.disallow_all = True
        elif 400 <= response.status_code < 500:
            # Missing robots.txt (typically 404/410) means there is no policy to apply.
            parser = None
        else:
            # Do not silently bypass an unavailable robots policy. Retryable 5xx
            # responses have already exhausted the configured retry budget here.
            response.raise_for_status()

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
        response = self._request_with_retries(url, host, **kwargs)
        response.raise_for_status()
        return response
