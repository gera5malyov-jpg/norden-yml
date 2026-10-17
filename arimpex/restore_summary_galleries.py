#!/usr/bin/env python3
"""Summary-only Arimpex gallery restore from the exact published 3895-offer YML.

Never downloads/uploads photographs. Never writes price/stock/SKU/features.
Modes:
  default: create full backup + deterministic plan (read-only)
  --apply: use that same saved plan, verify exact mapping, update only summary
"""
from __future__ import annotations

import argparse
import collections
import gzip
import html
import json
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from webasyst.client import WebasystClient

TYPE_ID = 96
FEED = ROOT / "arimpex" / "arimpex.yml"
DIR = ROOT / "reports"
BACKUP = DIR / "arimpex_extimg_before_20261017.json.gz"
PLAN = DIR / "arimpex_extimg_plan_20261017.json.gz"
REPORT = DIR / "arimpex_extimg_report_20261017.json"
TAG = re.compile(r"\[extimg\](.*?)\[/extimg\]", re.I | re.S)
URL = re.compile(r"https?://[^\s<>\[\]]+", re.I)


def key(s):
    return " ".join(unicodedata.normalize("NFKC", html.unescape(str(s or ""))).split()).casefold()


def listify(value):
    if isinstance(value, list):
        return [p for p in value if isinstance(p, dict)]
    if isinstance(value, dict):
        for k in ("products", "types", "items"):
            v = value.get(k)
            if isinstance(v, list):
                return [p for p in v if isinstance(p, dict)]
            if isinstance(v, dict):
                return [p for p in v.values() if isinstance(p, dict)]
        if value and all(isinstance(v, dict) for v in value.values()):
            return list(value.values())
    return []


def load_feed():
    offers = ET.parse(FEED).findall("./shop/offers/offer")
    if len(offers) < 3500:
        raise RuntimeError("Unexpectedly small Arimpex feed")
    result = {}
    names = set()
    photo_total = 0
    for offer in offers:
        name = offer.findtext("name", "")
        title = key(name)
        if not title or title in names:
            raise RuntimeError("Duplicate or empty Arimpex product name: " + name[:160])
        names.add(title)
        pics = []
        seen = set()
        for pic in offer.findall("picture"):
            u = (pic.text or "").strip()
            parts = urlsplit(u)
            if parts.scheme != "https" or parts.hostname not in ("arimpex.ru", "www.arimpex.ru", "imgproxy.arimpex.ru"):
                raise RuntimeError("Untrusted photo address for " + name[:80])
            if u not in seen:
                seen.add(u)
                pics.append(u)
        if not pics:
            raise RuntimeError("No pictures in feed for " + name[:120])
        result[title] = {"offer_id":offer.get("id"),"name":name,"images":pics}
        photo_total += len(pics)
    return result, photo_total


def load_products(wa):
    types = listify(wa.call("shop.type.getList"))
    selected = [t for t in types if str(t.get("name") or "").casefold() == "arimpex"]
    if len(selected) != 1 or int(selected[0]["id"]) != TYPE_ID:
        raise RuntimeError("Unexpected Arimpex Webasyst type mapping")
    products = []
    offset = 0
    limit = 1000
    while True:
        response = wa.call("shop.product.search",params={
            "hash":"type/"+str(TYPE_ID),"offset":offset,"limit":limit,
            "fields":"id,name,type_id,summary"})
        batch = listify(response)
        if any(int(p.get("type_id") or 0) != TYPE_ID for p in batch):
            raise RuntimeError("Webasyst returned product from a different type")
        products.extend(batch)
        if len(batch) < limit:
            break
        offset += len(batch)
        if offset > 10000:
            raise RuntimeError("Arimpex pagination limit exceeded")
    if len(products) < 3500 or len(products) != len({int(p["id"]) for p in products}):
        raise RuntimeError("Unexpected Arimpex product count or duplicates")
    return products


def valid_existing(s):
    if not s.strip():
        return True
    blocks = TAG.findall(s)
    if not blocks or len(blocks) != s.lower().count("[extimg]"):
        return False
    if re.sub(TAG, "", s).strip():
        return False
    return all(len(URL.findall(block)) == 1 for block in blocks)


def render(urls):
    return "\n".join("[extimg]\n" + u + "\n[/extimg]" for u in urls)


def plan(wa):
    feed, total_images = load_feed()
    products = load_products(wa)
    backup = []
    changes = []
    errors = []
    used = collections.Counter()
    already = 0
    for p in products:
        pid = int(p["id"])
        name = str(p.get("name") or "")
        s = str(p.get("summary") or "")
        backup.append({"id":pid,"type_id":TYPE_ID,"name":name,"summary":s})
        item = feed.get(key(name))
        if not item:
            errors.append({"id":pid,"name":name[:130],"reason":"no_exact_YML_title"})
            continue
        used[item["offer_id"]] += 1
        if not valid_existing(s):
            errors.append({"id":pid,"name":name[:130],"reason":"protected_manual_summary"})
            continue
        target = render(item["images"])
        if s.strip() == target:
            already += 1
        else:
            changes.append({"id":pid,"type_id":TYPE_ID,"name":name,
                "offer_id":item["offer_id"],"before":s,
                "after":target,"images":len(item["images"])})
    missing = sorted(set(x["offer_id"] for x in feed.values()) - set(used))
    duplicate_mappings = sorted(x for x,n in used.items() if n != 1)
    if len(feed) != len(products):
        errors.append({"reason":"catalog_size_mismatch","feed":len(feed),"webasyst":len(products)})
    if missing:
        errors.append({"reason":"missing_offers","count":len(missing),"examples":missing[:12]})
    if duplicate_mappings:
        errors.append({"reason":"duplicate_offer_mapping","examples":duplicate_mappings[:12]})
    DIR.mkdir(parents=True, exist_ok=True)
    metadata = {"run_time_utc":datetime.now(timezone.utc).isoformat(),
        "type_id":TYPE_ID,"feed_offers":len(feed),"shop_products":len(products),
        "feed_images":total_images,"existing_correct":already,
        "changes":len(changes),"change_image_links":sum(c["images"] for c in changes),
        "errors":errors[:30],"error_count":len(errors)}
    with gzip.open(BACKUP,"wt",encoding="utf-8") as f:
        json.dump({"metadata":metadata,"original_product_summaries":backup},f,ensure_ascii=False)
    with gzip.open(PLAN,"wt",encoding="utf-8") as f:
        json.dump({"metadata":metadata,"changes":changes},f,ensure_ascii=False)
    metadata["backup_bytes"] = BACKUP.stat().st_size
    metadata["plan_bytes"] = PLAN.stat().st_size
    REPORT.write_text(json.dumps({"mode":"plan","metadata":metadata,"updated":0,"verified":0},ensure_ascii=False,indent=2),encoding="utf-8")
    print("ARIMPEX_BACKUP_READY",json.dumps({"original_summaries":len(backup),
       "path":str(BACKUP),"bytes":BACKUP.stat().st_size}),ensure_ascii=False,flush=True)
    print("ARIMPEX_PLAN",json.dumps(metadata,ensure_ascii=False),flush=True)
    if errors:
        raise RuntimeError("Unsafe Arimpex mapping: investigate plan report before applying")
    return metadata


def apply(wa):
    if not BACKUP.is_file() or not PLAN.is_file():
        raise RuntimeError("Verified backup and immutable plan required")
    with gzip.open(PLAN,"rt",encoding="utf-8") as f:
        data=json.load(f)
    meta=data["metadata"]
    changes=data["changes"]
    if meta["error_count"] or len(changes) != meta["changes"]:
        raise RuntimeError("Plan is invalid, write blocked")
    current=load_products(wa)
    cmap={int(p["id"]):p for p in current}
    if len(current) != meta["shop_products"]:
        raise RuntimeError("Catalog changed after backup")
    # Atomic preflight before any writes. Refuse if importer changed even one summary.
    for item in changes:
        p=cmap.get(item["id"])
        if (not p or str(p.get("name") or "") != item["name"]
                or int(p.get("type_id") or 0) != TYPE_ID
                or str(p.get("summary") or "") != item["before"]):
            raise RuntimeError("Concurrent product modification before apply; product ID "+str(item["id"]))
    confirmed = 0
    failed = []
    for index,item in enumerate(changes,1):
        try:
            wa.call("shop.product.update",http_method="POST",
                params={"id":str(item["id"])}, data={"summary":item["after"]})
            confirmed += 1
        except Exception as exc:
            failed.append({"id":item["id"],"reason":type(exc).__name__,"detail":str(exc)[:140]})
            print("ARIMPEX_UPDATE_ERROR",json.dumps(failed[-1],ensure_ascii=False),flush=True)
            if len(failed) >= 5:
                break
        if index % 100 == 0 or index == len(changes):
            print("ARIMPEX_PROGRESS",json.dumps({"processed":index,
                "requested":len(changes),"success":confirmed,"errors":len(failed)}),flush=True)
            REPORT.write_text(json.dumps({"mode":"in_progress","metadata":meta,
                "updated":confirmed,"errors":failed},ensure_ascii=False,indent=2),encoding="utf-8")
    # Read back all product summaries; a successful API response alone is not enough.
    actual={int(p["id"]):str(p.get("summary") or "") for p in load_products(wa)}
    verified=sum(actual.get(c["id"])==c["after"] for c in changes)
    mismatches=[c["id"] for c in changes if actual.get(c["id"])!=c["after"]]
    result={"mode":"completed" if not failed and not mismatches else "partial_failure",
       "metadata":meta,"updated":confirmed,"verified":verified,
       "failed_count":len(failed),"failed":failed,"mismatch_ids":mismatches[:50],
       "rollback":str(BACKUP)}
    REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print("ARIMPEX_IMAGE_FINAL",json.dumps(result,ensure_ascii=False),flush=True)
    if failed or mismatches:
        raise RuntimeError("Arimpex image update did not fully verify")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply",action="store_true")
    args = ap.parse_args()
    wa = WebasystClient(min_request_interval=0.2)
    if args.apply:
        apply(wa)
    else:
        plan(wa)
