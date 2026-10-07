#!/usr/bin/env python3
import json
import re
import runpy
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET

PUBLIC_YML = "https://price.tetchair.ru/feeds/tetchair-msk-yml.xml"
OUT = Path("tetchair/tetchair-combined.xml")
REPORT = Path("tetchair/tetchair-combined-report.json")


def number(text):
    digits = re.sub(r"[^0-9]", "", text or "")
    return int(digits) if digits else None


def parse_card(a, base):
    text = " ".join(a.get_text(" ", strip=True).split())
    code = re.search(r"Код:\s*(\d+)", text)
    wholesale = re.search(r"Цена\s*опт:\s*([0-9\s\u00a0]+)\s*р", text, re.I)
    retail = re.search(r"СЦ:\s*([0-9\s\u00a0]+)\s*р", text, re.I)
    msk = re.search(r"МСК:\s*(.*?)\s+СПБ:", text, re.I)
    if not code:
        return None
    return {
        "code": code.group(1),
        "wholesale": number(wholesale.group(1)) if wholesale else None,
        "catalog_retail": number(retail.group(1)) if retail else None,
        "msk": (msk.group(1).strip() if msk else ""),
        "url": urljoin(base, a.get("href") or ""),
    }


def add_after(parent, after_tag, tag, value):
    node = parent.find(tag)
    if node is None:
        node = ET.Element(tag)
        children = list(parent)
        pos = next((i for i, x in enumerate(children) if x.tag == after_tag), None)
        if pos is None:
            parent.append(node)
        else:
            parent.insert(pos + 1, node)
    node.text = str(value)


def main():
    ns = runpy.run_path("tetchair/inspect_site.py", init_globals={"re": re})
    session = ns["s"]
    base = ns["BASE"]

    root_page = session.get(base + "/catalog/", timeout=60)
    root_page.raise_for_status()
    root_soup = BeautifulSoup(root_page.text, "html.parser")

    categories = []
    for a in root_soup.find_all("a", href=True):
        href = urljoin(base, a["href"])
        if re.fullmatch(r"https://price\.tetchair\.ru/catalog/\d+/", href) and href not in categories:
            categories.append(href)

    catalog = {}
    cards_seen = 0
    catalog_pages = 0
    per_category_pages = {}
    for idx, url in enumerate(categories, 1):
        queue = [url]
        seen_pages = set()
        category_pages = 0
        base_parts = urlparse(url)
        while queue and len(seen_pages) < 100:
            page_url = queue.pop(0)
            if page_url in seen_pages:
                continue
            seen_pages.add(page_url)
            r = session.get(page_url, timeout=60)
            r.raise_for_status()
            catalog_pages += 1
            category_pages += 1
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select('a[href*="?p="]'):
                item = parse_card(a, base)
                if not item:
                    continue
                cards_seen += 1
                prev = catalog.get(item["code"])
                if prev is None or (prev.get("wholesale") is None and item.get("wholesale") is not None):
                    catalog[item["code"]] = item

            # Follow any same-category navigation links, but never product detail links.
            for a in soup.find_all("a", href=True):
                href = urljoin(page_url, a.get("href") or "")
                parts = urlparse(href)
                if parts.scheme not in ("http", "https"):
                    continue
                if parts.netloc != base_parts.netloc or parts.path != base_parts.path:
                    continue
                q = parse_qs(parts.query, keep_blank_values=True)
                if "p" in q:
                    continue
                if not parts.query:
                    continue
                if href not in seen_pages and href not in queue:
                    queue.append(href)
        per_category_pages[base_parts.path] = category_pages
        print(
            f"catalog {idx}/{len(categories)} pages={category_pages} unique={len(catalog)}",
            flush=True,
        )

    yml = requests.get(PUBLIC_YML, timeout=120)
    yml.raise_for_status()
    xml_root = ET.fromstring(yml.content)
    offers = xml_root.findall(".//offer")

    matched = 0
    missing = []
    retail_mismatch = []
    for offer in offers:
        vc = offer.find("vendorCode")
        code = (vc.text or "").strip() if vc is not None else str(offer.get("id") or "").strip()
        price_el = offer.find("price")
        retail = number(price_el.text if price_el is not None else "")
        item = catalog.get(code)
        if retail is not None:
            add_after(offer, "price", "retail_price", retail)
        if item and item.get("wholesale") is not None:
            add_after(offer, "retail_price", "purchase_price", item["wholesale"])
            add_after(offer, "purchase_price", "catalog_url", item["url"])
            matched += 1
            if retail and item.get("catalog_retail") and retail != item["catalog_retail"]:
                retail_mismatch.append({
                    "code": code,
                    "yml": retail,
                    "catalog": item["catalog_retail"],
                })
        else:
            missing.append(code)

    xml_root.set("date", datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(xml_root).write(OUT, encoding="utf-8", xml_declaration=True)

    report = {
        "status": "ok",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "public_yml": PUBLIC_YML,
        "offers": len(offers),
        "categories": len(categories),
        "cards_seen": cards_seen,
        "catalog_pages": catalog_pages,
        "multi_page_categories": sum(1 for x in per_category_pages.values() if x > 1),
        "catalog_unique": len(catalog),
        "matched_wholesale": matched,
        "missing_wholesale": len(missing),
        "coverage": round(matched / len(offers), 6) if offers else 0,
        "missing_sample": missing[:100],
        "retail_mismatch_count": len(retail_mismatch),
        "retail_mismatch_sample": retail_mismatch[:100],
        "output": str(OUT),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("COMBINED_REPORT")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if not offers or not matched:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
