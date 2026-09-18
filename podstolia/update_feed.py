#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

SOURCE_URL = "https://podstolia.ru/modules/shop/shop.all.php"
BASE_DIR = Path(__file__).resolve().parent
PRICE_FILE = BASE_DIR / "prices.json"
OUTPUT_FILE = BASE_DIR / "podstolia.yml"

OFFER_RE = re.compile(br"<offer\b[^>]*>[\s\S]*?</offer>", re.I)
VENDOR_RE = re.compile(br"<vendorcode>\s*#?([^<\s]+)\s*</vendorcode>", re.I)
PRICE_RE = re.compile(br"(<price>[^<]*</price>)", re.I)
PURCHASE_RE = re.compile(br"<purchase_price>[^<]*</purchase_price>", re.I)

def fetch_source() -> bytes:
    req = urllib.request.Request(
        SOURCE_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Megapolis-PODSTOLIA-feed/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        return response.read()

def main() -> None:
    data = json.loads(PRICE_FILE.read_text(encoding="utf-8"))
    prices = {str(k).upper(): v for k, v in data["prices"].items()}

    source = fetch_source()
    if b"<offers>" not in source:
        raise RuntimeError("Supplier feed does not contain <offers>")

    stats = {"offers": 0, "priced": 0, "missing": [], "waiting": []}

    def enrich(block_match: re.Match[bytes]) -> bytes:
        block = block_match.group(0)
        stats["offers"] += 1

        vendor_match = VENDOR_RE.search(block)
        if not vendor_match:
            return block

        code = vendor_match.group(1).decode("ascii").strip().lstrip("#").upper()

        if code not in prices:
            stats["missing"].append(code)
            return block

        purchase_price = prices[code]
        if purchase_price is None:
            stats["waiting"].append(code)
            return block

        stats["priced"] += 1
        value = f"<purchase_price>{int(purchase_price)}</purchase_price>".encode("ascii")

        if PURCHASE_RE.search(block):
            return PURCHASE_RE.sub(value, block, count=1)

        if not PRICE_RE.search(block):
            raise RuntimeError(f"Offer {code} has no <price> tag")

        return PRICE_RE.sub(lambda m: m.group(1) + b"\n" + value, block, count=1)

    output = OFFER_RE.sub(enrich, source)

    # Critical rule: supplier bytes are preserved exactly.
    # No XML parsing, no reformatting, no encoding conversion.
    # We insert only ASCII <purchase_price>...</purchase_price> tags.
    purchase_count = len(re.findall(br"<purchase_price>", output, re.I))
    if stats["offers"] == 0 or purchase_count != stats["priced"]:
        raise RuntimeError(
            f"Validation failed: offers={stats['offers']}, "
            f"priced={stats['priced']}, purchase_price={purchase_count}"
        )

    OUTPUT_FILE.write_bytes(output)

    print(
        json.dumps(
            {
                "offers": stats["offers"],
                "purchase_prices": stats["priced"],
                "waiting_without_price": sorted(set(stats["waiting"])),
                "not_found_in_price_list": sorted(set(stats["missing"])),
                "output": str(OUTPUT_FILE),
            },
            ensure_ascii=False,
        )
    )

if __name__ == "__main__":
    main()
