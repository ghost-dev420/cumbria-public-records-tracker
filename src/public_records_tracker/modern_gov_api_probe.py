from __future__ import annotations

import argparse
import sys
from xml.etree import ElementTree

from .http import SafeHttpClient


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a public ModernGov XML endpoint")
    parser.add_argument("base_url")
    parser.add_argument("--operation", default="GetCommittees")
    parser.add_argument("--expect-tag", default="committees")
    args = parser.parse_args()

    url = f"{args.base_url.rstrip('/')}/mgWebService.asmx/{args.operation}"
    try:
        with SafeHttpClient() as client:
            response = client.get(
                url,
                headers={"Accept": "application/xml,text/xml;q=0.9,*/*;q=0.1"},
            )
        root = ElementTree.fromstring(response.content)
    except Exception as exc:
        print(f"API_PROBE_FAILED url={url} error={exc}")
        return 2

    tags = {_local_name(node.tag) for node in root.iter()}
    expected = args.expect_tag.casefold()
    found = expected in tags
    print(
        "API_PROBE_OK "
        f"status={response.status_code} url={response.url} bytes={len(response.content)} "
        f"expected_tag={expected} found={found} root={_local_name(root.tag)}"
    )
    if not found:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
