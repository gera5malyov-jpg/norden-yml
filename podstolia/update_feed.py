#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

SOURCE_URL = "https://podstolia.ru/modules/shop/shop.all.php"
BASE_DIR = Path(__file__).resolve().parent
PRICE_FILE = BASE_DIR / "prices.json"
OUTPUT_XML = BASE_DIR / "podstolia.xml"
OUTPUT_YML = BASE_DIR / "podstolia.yml"

OFFER_RE = re.compile(br"<offer\b[^>]*>[\s\S]*?</offer>", re.I)
VENDOR_RE = re.compile(br"<vendorCode>\s*#?([^<\s]+)\s*</vendorCode>", re.I)
PRICE_LINE_RE = re.compile(br"(?m)^([ \t]*)<price>([^<]*)</price>(\r?\n)")
PURCHASE_RE = re.compile(br"(?m)^([ \t]*)<purchase_price>[^<]*</purchase_price>(\r?\n)", re.I)

def fetch_source() -> bytes:
    req = urllib.request.Request(
        SOURCE_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Megapolis-PODSTOLIA-feed/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        raw = response.read()
    if b"<yml_catalog" not in raw or b"<offers>" not in raw or b"<vendorCode>" not in raw:
        raise RuntimeError("Supplier response is not the expected YML feed")
    return raw

def main() -> None:
    data = json.loads(PRICE_FILE.read_text(encoding="utf-8"))
    prices = {str(k).upper(): v for k, v in data["prices"].items()}
    source = fetch_source()

    stats = {"offers": 0, "inserted": 0, "waiting": [], "missing": [], "no_price": []}

    def enrich(match: re.Match[bytes]) -> bytes:
        block = match.group(0)
        stats["offers"] += 1

        vm = VENDOR_RE.search(block)
        if not vm:
            return block

        code = vm.group(1).decode("ascii").strip().lstrip("#").upper()

        if code not in prices:
            stats["missing"].append(code)
            return block

        purchase_price = prices[code]
        if purchase_price is None:
            stats["waiting"].append(code)
            return block

        block = PURCHASE_RE.sub(b"", block)

        pm = PRICE_LINE_RE.search(block)
        if not pm:
            stats["no_price"].append(code)
            return block

        indent = pm.group(1)
        newline = pm.group(3)
        addition = indent + f"<purchase_price>{int(purchase_price)}</purchase_price>".encode("ascii") + newline
        stats["inserted"] += 1
        return block[:pm.end()] + addition + block[pm.end():]

    output = OFFER_RE.sub(enrich, source)

    # Hard check: remove only the added purchase_price lines.
    # The remaining bytes must be exactly identical to the supplier response.
    restored = PURCHASE_RE.sub(b"", output)
    if restored != source:
        raise RuntimeError("Supplier feed changed beyond purchase_price insertion")

    if stats["offers"] == 0:
        raise RuntimeError("No offers found in supplier feed")
    if len(re.findall(br"<purchase_price>", output, re.I)) != stats["inserted"]:
        raise RuntimeError("purchase_price count validation failed")

    ET.fromstring(output)

    OUTPUT_XML.write_bytes(output)
    OUTPUT_YML.write_bytes(output)

    print(json.dumps({
        "offers": stats["offers"],
        "purchase_prices": stats["inserted"],
        "waiting_without_price": sorted(set(stats["waiting"])),
        "not_found_in_price_list": sorted(set(stats["missing"])),
        "offers_without_price_tag": sorted(set(stats["no_price"])),
        "source_encoding_header": source.splitlines()[0].decode("ascii", errors="replace"),
        "supplier_bytes_preserved_exactly": restored == source,
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
