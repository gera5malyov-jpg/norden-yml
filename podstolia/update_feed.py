#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

SOURCE_URL = "https://podstolia.ru/modules/shop/shop.all.php"
BASE_DIR = Path(__file__).resolve().parent
PRICE_FILE = BASE_DIR / "prices.json"
OUTPUT_XML = BASE_DIR / "podstolia.xml"
OUTPUT_YML = BASE_DIR / "podstolia.yml"

OFFER_RE = re.compile(r"<offer\b[^>]*>[\s\S]*?</offer>", re.I)
VENDOR_RE = re.compile(r"<vendorcode>\s*#?([^<\s]+)\s*</vendorcode>", re.I)
PRICE_RE = re.compile(r"(<price>[^<]*</price>)", re.I)
PURCHASE_RE = re.compile(r"<purchase_price>[^<]*</purchase_price>", re.I)
ENCODING_RE = re.compile(
    r'(<\?xml\s+version=["\']1\.0["\']\s+encoding=["\'])[^"\']+(["\'][^?]*\?>)',
    re.I,
)

def fetch_source() -> str:
    req = urllib.request.Request(
        SOURCE_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Megapolis-PODSTOLIA-feed/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        raw = response.read()

    head = raw[:300].decode("ascii", errors="ignore")
    match = re.search(r'encoding=["\']([^"\']+)["\']', head, re.I)
    encoding = match.group(1) if match else "windows-1251"
    return raw.decode(encoding, errors="strict")

def main() -> None:
    data = json.loads(PRICE_FILE.read_text(encoding="utf-8"))
    prices = {str(k).upper(): v for k, v in data["prices"].items()}
    source = fetch_source()

    if "<offers>" not in source:
        raise RuntimeError("Supplier feed does not contain <offers>")

    stats = {"offers": 0, "priced": 0, "missing": [], "waiting": []}

    def enrich(match: re.Match[str]) -> str:
        block = match.group(0)
        stats["offers"] += 1
        vm = VENDOR_RE.search(block)
        if not vm:
            return block

        code = vm.group(1).strip().lstrip("#").upper()
        if code not in prices:
            stats["missing"].append(code)
            return block

        purchase_price = prices[code]
        if purchase_price is None:
            stats["waiting"].append(code)
            return block

        stats["priced"] += 1
        value = f"<purchase_price>{int(purchase_price)}</purchase_price>"

        if PURCHASE_RE.search(block):
            return PURCHASE_RE.sub(value, block, count=1)

        if not PRICE_RE.search(block):
            raise RuntimeError(f"Offer {code} has no <price> tag")

        return PRICE_RE.sub(lambda m: m.group(1) + "\n" + value, block, count=1)

    output = OFFER_RE.sub(enrich, source)
    output = ENCODING_RE.sub(r"\1UTF-8\2", output, count=1)

    purchase_count = len(re.findall(r"<purchase_price>", output, re.I))
    if stats["offers"] == 0 or purchase_count != stats["priced"]:
        raise RuntimeError(
            f"Validation failed: offers={stats['offers']}, "
            f"priced={stats['priced']}, purchase_price={purchase_count}"
        )

    # YML is XML-based. UTF-8 makes the file reliably readable by browsers/importers.
    OUTPUT_XML.write_text(output, encoding="utf-8", newline="")
    OUTPUT_YML.write_text(output, encoding="utf-8", newline="")

    print(json.dumps({
        "offers": stats["offers"],
        "purchase_prices": stats["priced"],
        "waiting_without_price": sorted(set(stats["waiting"])),
        "not_found_in_price_list": sorted(set(stats["missing"])),
        "xml": str(OUTPUT_XML),
        "yml": str(OUTPUT_YML),
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
