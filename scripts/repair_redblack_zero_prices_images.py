#!/usr/bin/env python3
"""One-time, guarded repair of existing Red-Black prices/images.

All future supplier updates must be made through Webasyst Megasuppliers.
This migration never creates/deletes products or changes stocks/categories.
"""
import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

import requests
from webasyst.client import WebasystClient
from supplier_engine.adapters import load_xml_yml
from supplier_engine.runner import normalize
from supplier_engine.webasyst_sync import extimg_summary

FEED_URL = (
    "http://opt.red-black.ru/pricelist_api.xml?"
    "org=e15e8a25-8153-11e9-8be6-00155d396f01"
    "&showcategories=True&showpictures=True&showparams=True"
)


def listify(payload, keys=("products", "items", "skus")):
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if isinstance(payload, dict):
        for k in keys:
            v = payload.get(k)
            if isinstance(v, list):
                return [p for p in v if isinstance(p, dict)]
            if isinstance(v, dict):
                return list(v.values())
        if payload and all(isinstance(v, dict) for v in payload.values()):
            return list(payload.values())
    return []


def number(value):
    try:
        return float(str(value or "0").replace(",", "."))
    except (ValueError, TypeError):
        return 0.0


def source_products():
    response = requests.get(FEED_URL, timeout=(20, 240))
    response.raise_for_status()
    cfg = {
        "code": "RED_BLACK", "name": "Red-Black",
        "identity": {"supplier_sku_field": "article", "sku_prefix": "RED-00-"},
        "mapping": {"name": "name", "purchase_price": "price", "price": "rrc_price",
                    "stock": "stock", "category": "categoryId", "images": ["pictures"]},
        "rules": {},
    }
    products = normalize(cfg, load_xml_yml(response.content))
    if len(products) < 1000:
        raise RuntimeError("Red-Black source is unexpectedly small; stopping repair")
    result = defaultdict(list)
    for p in products:
        if p.sku and p.price and p.price > 0 and p.purchase_price and p.purchase_price > 0:
            result[p.sku].append(p)
    return result, len(products)


def existing_products(wa):
    types = listify(wa.call("shop.type.getList"))
    matches = [t for t in types if str(t.get("name") or t.get("title") or "").casefold().strip() == "red-black1"]
    if len(matches) != 1:
        raise RuntimeError("Cannot uniquely identify RED-Black1 product type")
    tid = str(matches[0]["id"])
    found = []
    for offset in range(0, 50000, 500):
        page = wa.call("shop.product.search", params={
            "hash": "type/" + tid, "limit": 500, "offset": offset,
            "fields": "id,name,type_id,summary,skus",
        })
        batch = listify(page, ("products", "items"))
        found.extend(batch)
        if len(batch) < 500:
            break
    return tid, found


def build_updates(wa):
    sources, feed_count = source_products()
    type_id, products = existing_products(wa)
    indexed = defaultdict(list)
    for product in products:
        for sku in listify(product.get("skus") or [], ("skus",)):
            code = str(sku.get("sku") or "").strip()
            if code.startswith("RED-"):
                indexed[code].append((product, sku))
    updates = []
    missing_source_zeros = []
    ambiguous = []
    for code, matches in indexed.items():
        if len(matches) != 1 or len(sources.get(code) or []) > 1:
            ambiguous.append(code)
            continue
        product, sku = matches[0]
        source_matches = sources.get(code) or []
        if not source_matches:
            if number(sku.get("price")) <= 0:
                missing_source_zeros.append(code)
            continue
        source = source_matches[0]
        is_zero = number(sku.get("price")) <= 0
        summary = str(product.get("summary") or "")
        image_urls = source.images or []
        needs_image = (
            bool(image_urls)
            and ("avatars.mds.yandex.net" in summary or ("\\n" in summary and "[extimg]" in summary))
            and extimg_summary(image_urls) != summary
        )
        if not (is_zero or needs_image):
            continue
        skus_in_product = [
            s for s in listify(product.get("skus") or [], ("skus",))
            if str(s.get("sku") or "").startswith("RED-")
        ]
        # One short description is shared by the entire product card.
        if len(skus_in_product) != 1:
            needs_image = False
        if not (is_zero or needs_image):
            continue
        updates.append({
            "sku": code, "sku_id": str(sku.get("id") or ""),
            "product_id": str(product.get("id") or ""),
            "old_price": sku.get("price"), "old_purchase": sku.get("purchase_price"),
            "new_price": source.price, "new_purchase": source.purchase_price,
            "old_summary": summary,
            "new_summary": extimg_summary(image_urls) if needs_image else None,
            "repair_price": is_zero,
            "repair_images": needs_image,
        })
    summary = {
        "feed_count": feed_count, "webasyst_type_id": type_id,
        "webasyst_products": len(products), "webasyst_red_skus": len(indexed),
        "repair_price": sum(int(v["repair_price"]) for v in updates),
        "repair_images": sum(int(v["repair_images"]) for v in updates),
        "no_source_zero_price": missing_source_zeros[:100],
        "ambiguous_skus": ambiguous[:100],
        "sample_price": [
            {"sku": v["sku"], "old": v["old_price"], "new": v["new_price"]}
            for v in updates if v["repair_price"]
        ][:40],
        "sample_images": [
            {"sku": v["sku"], "count": v["new_summary"].count("[extimg]")}
            for v in updates if v["repair_images"]
        ][:15],
    }
    if len(products) < 1500 or len(indexed) < 1500:
        raise RuntimeError("Webasyst Red-Black coverage unexpectedly low")
    if summary["repair_price"] > 100 or summary["repair_images"] > 1500:
        raise RuntimeError("Red-Black repair candidate count exceeded safety limit")
    if ambiguous:
        summary["warnings"] = ["Ambiguous SKU associations skipped"]
    return updates, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--sku", action="append", default=[], help="Limit writes to exact SKU")
    ap.add_argument("--backup", default="redblack-repair-backup.json")
    ap.add_argument("--report", default="redblack-repair-report.json")
    args = ap.parse_args()
    wa = WebasystClient(min_request_interval=0.1)
    updates, report = build_updates(wa)
    selected = [u for u in updates if not args.sku or u["sku"] in args.sku]
    backup = {
        "generated_by": "repair_redblack_zero_prices_images.py",
        "mode": "apply" if args.apply else "dry-run",
        "items": selected,
    }
    Path(args.backup).write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
    report["selected"] = len(selected)
    report["applied_prices"] = 0
    report["applied_images"] = 0
    report["failed"] = []
    if args.apply:
        for u in selected:
            if not u["sku_id"] or not u["product_id"]:
                report["failed"].append({"sku": u["sku"], "error": "missing_id"})
                continue
            try:
                if u["repair_price"]:
                    wa.call("shop.product.skus.update", http_method="POST",
                            params={"id": u["sku_id"]},
                            data={"price": str(u["new_price"]), "purchase_price": str(u["new_purchase"])})
                    report["applied_prices"] += 1
                if u["repair_images"]:
                    wa.call("shop.product.update", http_method="POST",
                            params={"id": u["product_id"]}, data={"summary": u["new_summary"]})
                    report["applied_images"] += 1
            except Exception as exc:
                report["failed"].append({"sku": u["sku"], "error": type(exc).__name__})
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("RED_BLACK_REPAIR_REPORT=" + json.dumps(report, ensure_ascii=False))
    if report["failed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
