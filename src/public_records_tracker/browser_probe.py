from __future__ import annotations

import argparse
import sys

from bs4 import BeautifulSoup

from .browser import BrowserHttpClient


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe a public page using the tracker's plain Chromium client"
    )
    parser.add_argument("url")
    parser.add_argument("--expect", default="Councillor")
    args = parser.parse_args()

    try:
        with BrowserHttpClient(delay=0.75, timeout=30.0) as client:
            response = client.get(args.url)
    except Exception as exc:
        print(f"PROBE_FAILED url={args.url} error={exc}")
        return 2

    text = BeautifulSoup(response.content, "html.parser").get_text(" ", strip=True)
    title_tag = BeautifulSoup(response.content, "html.parser").find("title")
    title = title_tag.get_text(" ", strip=True) if title_tag else ""
    found = args.expect.casefold() in text.casefold()
    print(
        "PROBE_OK "
        f"status={response.status_code} final_url={response.url} "
        f"bytes={len(response.content)} expected_text={found} title={title!r}"
    )
    if not found:
        print(f"Expected marker {args.expect!r} was not present in rendered page text")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
