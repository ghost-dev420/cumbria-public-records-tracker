from __future__ import annotations

import httpx
import pytest

from public_records_tracker.http import SafeHttpClient


def test_safe_http_retries_transport_failure(monkeypatch):
    client = SafeHttpClient(delay=0, respect_robots=False, retries=2, retry_backoff=0)
    calls = 0

    def fake_get(url, **kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ReadError("peer closed connection")
        return httpx.Response(200, request=httpx.Request("GET", url), content=b"ok")

    monkeypatch.setattr(client.client, "get", fake_get)
    response = client.get("https://example.test/file.csv")
    assert response.content == b"ok"
    assert calls == 3
    client.close()


def test_safe_http_retries_retryable_status(monkeypatch):
    client = SafeHttpClient(delay=0, respect_robots=False, retries=1, retry_backoff=0)
    calls = 0

    def fake_get(url, **kwargs):
        nonlocal calls
        calls += 1
        status = 503 if calls == 1 else 200
        return httpx.Response(status, request=httpx.Request("GET", url), content=b"ok")

    monkeypatch.setattr(client.client, "get", fake_get)
    assert client.get("https://example.test/file.pdf").status_code == 200
    assert calls == 2
    client.close()


def test_robots_transport_failure_is_retried_and_propagated(monkeypatch):
    client = SafeHttpClient(delay=0, respect_robots=True, retries=1, retry_backoff=0)
    calls = 0

    def fake_get(url, **kwargs):
        nonlocal calls
        calls += 1
        request = httpx.Request("GET", url)
        raise httpx.ConnectError("temporary failure in name resolution", request=request)

    monkeypatch.setattr(client.client, "get", fake_get)
    with pytest.raises(httpx.ConnectError):
        client.get("https://example.test/file.csv")
    assert calls == 2
    client.close()


def test_missing_robots_file_allows_target_fetch(monkeypatch):
    client = SafeHttpClient(delay=0, respect_robots=True, retries=0, retry_backoff=0)
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        status = 404 if url.endswith("/robots.txt") else 200
        return httpx.Response(status, request=httpx.Request("GET", url), content=b"ok")

    monkeypatch.setattr(client.client, "get", fake_get)
    assert client.get("https://example.test/file.csv").status_code == 200
    assert calls == ["https://example.test/robots.txt", "https://example.test/file.csv"]
    client.close()


def test_forbidden_robots_file_blocks_target_fetch(monkeypatch):
    client = SafeHttpClient(delay=0, respect_robots=True, retries=0, retry_backoff=0)
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return httpx.Response(403, request=httpx.Request("GET", url), content=b"")

    monkeypatch.setattr(client.client, "get", fake_get)
    with pytest.raises(PermissionError):
        client.get("https://example.test/file.csv")
    assert calls == ["https://example.test/robots.txt"]
    client.close()
