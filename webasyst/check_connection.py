#!/usr/bin/env python3
import json
import os
import sys

from client import WebasystAPIError, WebasystClient


def main() -> int:
    base_url = os.getenv("WEBASYST_BASE_URL", "https://profikompany.ru")
    print(f"Checking Webasyst API at {base_url}")

    try:
        client = WebasystClient(base_url=base_url)
        product_types = client.call("shop.type.getList")
    except WebasystAPIError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not isinstance(product_types, (list, dict)):
        print("ERROR: unexpected response type", file=sys.stderr)
        return 1

    if isinstance(product_types, list):
        count = len(product_types)
    else:
        count = len(product_types.keys())

    result = {
        "ok": True,
        "base_url": base_url,
        "tested_method": "shop.type.getList",
        "product_type_count": count,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("Webasyst API connection is working.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
