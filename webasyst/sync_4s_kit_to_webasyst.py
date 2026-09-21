#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import mimetypes
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlparse

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from client import WebasystAPIError, WebasystClient

TYPE_NAME = "33 Кровати-333"
EXPECTED_TYPE_ID = "79"
WA_STOCK_NAME = "33кровати"
EXPECTED_WA_STOCK_ID = "68"
KIT_STOCK_NAME = "СПБ"
BRAND = "4 Сезона"
ARTICLE_TITLE = "Артикул"
CODE_SITE_TITLE = "Код для сайта"
FEED = ROOT / "4s-mebel.yml"
REPORT = HERE / "last_4s_kit_sync_report.json"
MONEY = Decimal("0.01")
MAX_IMAGES = int(os.getenv("FOURS_WEBASYST_MAX_IMAGES", "10"))
WRITE_DELAY = float(os.getenv("FOURS_WEBASYST_WRITE_DELAY", "0.65"))
DRY_RUN = str(os.getenv("DRY_RUN", "1")).strip().lower() not in {"0", "false", "no", "off"}
MAX_WRITES = int(os.getenv("MAX_WRITES", "0") or "0")


def s(v):
    return str(v or "").strip()


def norm(v):
    return re.sub(
        r"[^0-9a-zа-яё]+",
        "",
        unicodedata.normalize("NFKC", s(v)).casefold(),
    )


def money(v):
    if v in (None, ""):
        return None
    try:
        d = Decimal(str(v).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if d < 0:
        return None
    return d.quantize(MONEY, rounding=ROUND_HALF_UP)


def money_str(v):
    d = money(v)
    return None if d is None else f"{d:.2f}"


def as_int(v, default=0):
    try:
        return max(0, int(Decimal(str(v or 0))))
    except Exception:
        return default


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_kit_module():
    path = ROOT / "4s-kit" / "sync_4s_kit.py"
    spec = importlib.util.spec_from_file_location("sync_4s_kit_module", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def listify(payload, keys=()):
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            return [x for x in value.values() if isinstance(x, dict)]
    # Webasyst often returns id-keyed maps.
    if payload and all(isinstance(v, dict) for v in payload.values()):
        return list(payload.values())
    return []


def exact_one(rows, wanted, *, label):
    matches = [
        row for row in rows
        if norm(row.get("name") or row.get("title")) == norm(wanted)
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one {label} {wanted!r}; found {len(matches)}")
    return matches[0]


def char_value(variant, cid):
    for row in variant.get("characteristics") or []:
        if s(row.get("characteristic_id")) != s(cid):
            continue
        vals = row.get("values") or []
        return s(row.get("value")) or (s(vals[0]) if vals else "")
    return ""


def kit_stock_qty(variant, warehouse_id):
    for row in variant.get("stocks") or []:
        if s(row.get("warehouse_id")) == s(warehouse_id):
            return as_int(row.get("quantity"))
    return 0


def desired_prices(variant):
    pricing = variant.get("pricing") or {}
    # In KIT: manual_discount_price = "Цена для покупателя";
    # pricing.price = "Цена до скидки".
    customer = money(pricing.get("manual_discount_price"))
    compare = money(pricing.get("price"))
    if customer is None or customer <= 0:
        # Never invent a sale price. If KIT has no customer price, skip price write.
        return None
    purchase = (customer * Decimal("0.80")).quantize(MONEY, rounding=ROUND_HALF_UP)
    if compare is None or compare < customer:
        compare = Decimal("0.00")
    return {
        "price": customer,
        "compare_price": compare,
        "purchase_price": purchase,
    }


def parse_feed():
    by_code = defaultdict(list)
    if not FEED.exists():
        return by_code
    for event, elem in ET.iterparse(FEED, events=("end",)):
        if elem.tag != "offer":
            continue
        vendor = s(elem.findtext("vendor"))
        if vendor != BRAND:
            elem.clear()
            continue
        code = s(elem.findtext("vendorCode")) or s(elem.attrib.get("id"))
        if code:
            by_code[norm(code)].append({
                "code": code,
                "name": s(elem.findtext("name")),
                "description": s(elem.findtext("description")),
                "url": s(elem.findtext("url")),
                "pictures": list(dict.fromkeys(
                    s(x.text) for x in elem.findall("picture") if s(x.text)
                )),
                "params": {
                    s(x.attrib.get("name")): s(x.text)
                    for x in elem.findall("param")
                    if s(x.attrib.get("name")) and s(x.text)
                },
            })
        elem.clear()
    return by_code


def feed_one(feed_index, code_site):
    rows = feed_index.get(norm(code_site), [])
    return rows[0] if len(rows) == 1 else None


def load_wa_products(wa, type_id):
    out = []
    offset = 0
    while True:
        payload = wa.call(
            "shop.product.search",
            params={
                "hash": f"type/{type_id}",
                "offset": offset,
                "limit": 1000,
                "fields": "*,skus,stock_counts",
            },
        )
        batch = listify(payload, ("products", "items"))
        total = None
        if isinstance(payload, dict):
            total = payload.get("count") or payload.get("total_count")
        out.extend(batch)
        if not batch or len(batch) < 1000:
            break
        if total not in (None, "") and len(out) >= int(total):
            break
        offset += len(batch)
    return out


def product_skus(product):
    skus = product.get("skus")
    if isinstance(skus, dict):
        return [x for x in skus.values() if isinstance(x, dict)]
    if isinstance(skus, list):
        return [x for x in skus if isinstance(x, dict)]
    return []


def wa_stock_qty(sku, stock_id):
    stock = sku.get("stock")
    if isinstance(stock, dict):
        if stock_id in stock:
            return as_int(stock.get(stock_id))
        if str(stock_id) in stock:
            return as_int(stock.get(str(stock_id)))
    if isinstance(stock, list):
        for row in stock:
            if isinstance(row, dict) and s(row.get("stock_id") or row.get("id")) == s(stock_id):
                return as_int(row.get("count") if "count" in row else row.get("quantity"))
    return None


def get_product_skus(wa, product_id):
    payload = wa.call("shop.product.skus.getList", params={"product_id": product_id})
    return listify(payload, ("skus", "items"))


def extract_product_id(payload):
    if isinstance(payload, dict):
        for key in ("id", "product_id"):
            if payload.get(key) not in (None, ""):
                return s(payload.get(key))
        product = payload.get("product")
        if isinstance(product, dict) and product.get("id") not in (None, ""):
            return s(product.get("id"))
    return ""


def feature_defs(wa, type_id):
    try:
        payload = wa.call("shop.feature.getList", params={"type_id": type_id})
    except Exception as exc:
        print(f"Feature definitions unavailable: {exc}", flush=True)
        return [], {}
    rows = listify(payload, ("features", "items"))
    by_title = defaultdict(list)
    for row in rows:
        by_title[norm(row.get("name") or row.get("title"))].append(row)
    return rows, by_title


def combined_source_features(variant, kit_char_by_id, feed):
    out = {}
    # KIT is primary.
    for entry in variant.get("characteristics") or []:
        cid = s(entry.get("characteristic_id"))
        title = s((kit_char_by_id.get(cid) or {}).get("title"))
        value = char_value(variant, cid)
        if title and value:
            out.setdefault(title, value)
    # Current 4s parser supplements fields that may not have been copied into KIT.
    if feed:
        for title, value in (feed.get("params") or {}).items():
            if title and value:
                out.setdefault(title, value)
    out.setdefault("Бренд", BRAND)
    return out


def safe_feature_payload(source, wa_features_by_title):
    out = {}
    skipped = 0
    for title, value in source.items():
        matches = wa_features_by_title.get(norm(title), [])
        if len(matches) != 1:
            skipped += 1
            continue
        row = matches[0]
        code = s(row.get("code"))
        ftype = s(row.get("type")).lower()
        selectable = bool(row.get("selectable"))
        if not code or selectable:
            skipped += 1
            continue
        # Keep automatic writes conservative: scalar text/numeric fields only.
        if ftype and not any(x in ftype for x in ("varchar", "text", "double", "float", "int", "decimal")):
            skipped += 1
            continue
        out[code] = value
    return out, skipped


def download_image(url):
    r = requests.get(
        url,
        timeout=90,
        headers={"User-Agent": "Mozilla/5.0 4s-webasyst-sync/1.0"},
    )
    r.raise_for_status()
    name = os.path.basename(urlparse(url).path) or "image.jpg"
    mime = (r.headers.get("Content-Type") or mimetypes.guess_type(name)[0] or "image/jpeg").split(";")[0]
    return name, r.content, mime


def upload_images(wa, product_id, urls, report):
    for url in list(dict.fromkeys(urls))[:MAX_IMAGES]:
        try:
            name, content, mime = download_image(url)
            wa.call(
                "shop.product.images.addMany",
                http_method="POST",
                params={"product_id": product_id},
                files=[("files[]", (name, content, mime))],
            )
            report["images_uploaded"] += 1
        except Exception as exc:
            report["image_errors"] += 1
            if len(report["errors"]) < 300:
                report["errors"].append({
                    "stage": "image",
                    "product_id": product_id,
                    "url": url,
                    "message": str(exc)[:500],
                })


def sku_update_payload(current, desired, stock_id):
    changes = {}
    cur_price = money(current.get("price"))
    cur_compare = money(current.get("compare_price"))
    cur_purchase = money(current.get("purchase_price"))
    if cur_price != desired["price"]:
        changes["price"] = money_str(desired["price"])
    if cur_compare != desired["compare_price"]:
        changes["compare_price"] = money_str(desired["compare_price"])
    if cur_purchase != desired["purchase_price"]:
        changes["purchase_price"] = money_str(desired["purchase_price"])
    cur_stock = wa_stock_qty(current, stock_id)
    if cur_stock is None or cur_stock != desired["stock"]:
        changes["stock"] = {str(stock_id): str(desired["stock"])}
    return changes


def main():
    started = now_iso()
    kit_mod = load_kit_module()
    kit = kit_mod.Kit(os.getenv("YANDEX_KIT_TOKEN", ""))
    wa = WebasystClient(min_request_interval=WRITE_DELAY)

    report = {
        "started_at": started,
        "dry_run": DRY_RUN,
        "type_name": TYPE_NAME,
        "type_id": None,
        "webasyst_stock": WA_STOCK_NAME,
        "webasyst_stock_id": None,
        "kit_stock": KIT_STOCK_NAME,
        "kit_stock_id": None,
        "kit_brand_variants": 0,
        "kit_unique_articles": 0,
        "webasyst_products": 0,
        "webasyst_skus": 0,
        "matched": 0,
        "unchanged": 0,
        "existing_to_update": 0,
        "existing_updated": 0,
        "new_to_create": 0,
        "new_created": 0,
        "new_partial": 0,
        "kit_duplicate_articles": 0,
        "webasyst_duplicate_articles": 0,
        "missing_kit_article": 0,
        "missing_price": 0,
        "feature_values_written": 0,
        "feature_values_skipped": 0,
        "images_uploaded": 0,
        "image_errors": 0,
        "writes": 0,
        "errors": [],
        "sample_updates": [],
        "sample_new": [],
    }

    # Resolve guarded target objects.
    types = listify(wa.call("shop.type.getList"))
    target_type = exact_one(types, TYPE_NAME, label="Webasyst product type")
    type_id = s(target_type.get("id"))
    if type_id != EXPECTED_TYPE_ID:
        raise RuntimeError(f"Safety stop: {TYPE_NAME!r} changed id from {EXPECTED_TYPE_ID} to {type_id}")
    report["type_id"] = type_id

    wa_stocks = listify(wa.call("shop.stock.getList"))
    wa_stock = exact_one(wa_stocks, WA_STOCK_NAME, label="Webasyst stock")
    wa_stock_id = s(wa_stock.get("id"))
    if wa_stock_id != EXPECTED_WA_STOCK_ID:
        raise RuntimeError(f"Safety stop: stock {WA_STOCK_NAME!r} changed id from {EXPECTED_WA_STOCK_ID} to {wa_stock_id}")
    report["webasyst_stock_id"] = wa_stock_id

    kit_warehouses = kit.warehouses()
    kit_stock = exact_one(kit_warehouses, KIT_STOCK_NAME, label="KIT stock")
    kit_stock_id = s(kit_stock.get("id"))
    report["kit_stock_id"] = kit_stock_id

    kit_chars = kit.characteristics()
    char_by_id = {s(x.get("id")): x for x in kit_chars if s(x.get("id"))}
    article_char = exact_one(kit_chars, ARTICLE_TITLE, label="KIT characteristic")
    article_id = s(article_char.get("id"))
    code_site_char = exact_one(kit_chars, CODE_SITE_TITLE, label="KIT characteristic")
    code_site_id = s(code_site_char.get("id"))

    feed = parse_feed()
    _, wa_features_by_title = feature_defs(wa, type_id)

    # KIT scan: only brand 4 Сезона. Duplicate articles are quarantined.
    kit_by_article = defaultdict(list)
    for variant in kit.variants():
        if s(variant.get("brand")) != BRAND:
            continue
        report["kit_brand_variants"] += 1
        article = char_value(variant, article_id)
        if not article:
            report["missing_kit_article"] += 1
            continue
        kit_by_article[norm(article)].append(variant)

    kit_duplicates = {k: rows for k, rows in kit_by_article.items() if len(rows) > 1}
    report["kit_duplicate_articles"] = len(kit_duplicates)
    kit_unique = {k: rows[0] for k, rows in kit_by_article.items() if len(rows) == 1}
    report["kit_unique_articles"] = len(kit_unique)

    # Webasyst scan is restricted by type ID 79. No other product type can be changed.
    products = load_wa_products(wa, type_id)
    report["webasyst_products"] = len(products)
    wa_by_article = defaultdict(list)
    for product in products:
        for sku in product_skus(product):
            report["webasyst_skus"] += 1
            article = s(sku.get("sku"))
            if article:
                wa_by_article[norm(article)].append((product, sku))

    wa_duplicates = {k: rows for k, rows in wa_by_article.items() if len(rows) > 1}
    report["webasyst_duplicate_articles"] = len(wa_duplicates)

    write_count = 0

    def allow_write():
        nonlocal write_count
        if DRY_RUN:
            return False
        if MAX_WRITES and write_count >= MAX_WRITES:
            raise RuntimeError(f"MAX_WRITES safety limit reached: {MAX_WRITES}")
        write_count += 1
        report["writes"] = write_count
        return True

    # Existing cards: ONLY price, compare price, purchase price, and target stock.
    for article_key, variant in kit_unique.items():
        if article_key in kit_duplicates or article_key in wa_duplicates:
            continue

        rows = wa_by_article.get(article_key, [])
        prices = desired_prices(variant)
        if prices is None:
            report["missing_price"] += 1
            continue
        desired = {
            **prices,
            "stock": kit_stock_qty(variant, kit_stock_id),
        }

        if rows:
            report["matched"] += 1
            product, sku = rows[0]
            changes = sku_update_payload(sku, desired, wa_stock_id)
            if not changes:
                report["unchanged"] += 1
                continue

            report["existing_to_update"] += 1
            if len(report["sample_updates"]) < 25:
                report["sample_updates"].append({
                    "article": s(sku.get("sku")),
                    "product_id": s(product.get("id")),
                    "sku_id": s(sku.get("id")),
                    "fields": sorted(changes.keys()),
                    "desired_price": money_str(desired["price"]),
                    "desired_compare_price": money_str(desired["compare_price"]),
                    "desired_purchase_price": money_str(desired["purchase_price"]),
                    "desired_stock": desired["stock"],
                })

            if allow_write():
                try:
                    wa.call(
                        "shop.product.skus.update",
                        http_method="POST",
                        params={"id": s(sku.get("id"))},
                        data=changes,
                    )
                    report["existing_updated"] += 1
                except Exception as exc:
                    if len(report["errors"]) < 300:
                        report["errors"].append({
                            "stage": "update_existing",
                            "article": s(sku.get("sku")),
                            "product_id": s(product.get("id")),
                            "sku_id": s(sku.get("id")),
                            "message": str(exc)[:500],
                        })
            continue

        # New card: create full card from KIT + current parser feed.
        report["new_to_create"] += 1
        article = char_value(variant, article_id)
        code_site = char_value(variant, code_site_id)
        feed_row = feed_one(feed, code_site)
        name = s(variant.get("name")) or (s(feed_row.get("name")) if feed_row else "") or article
        description = s(variant.get("description")) or (s(feed_row.get("description")) if feed_row else "")
        pictures = (feed_row.get("pictures") or []) if feed_row else []
        source_features = combined_source_features(variant, char_by_id, feed_row)
        features, skipped_features = safe_feature_payload(source_features, wa_features_by_title)
        report["feature_values_skipped"] += skipped_features

        if len(report["sample_new"]) < 25:
            report["sample_new"].append({
                "article": article,
                "kit_variant_id": s(variant.get("id")),
                "kit_code_site": code_site,
                "name": name,
                "price": money_str(desired["price"]),
                "compare_price": money_str(desired["compare_price"]),
                "purchase_price": money_str(desired["purchase_price"]),
                "stock": desired["stock"],
                "feed_found": bool(feed_row),
                "images": len(pictures),
                "mapped_features": len(features),
            })

        if DRY_RUN:
            continue

        product_id = ""
        try:
            if not allow_write():
                continue
            add_payload = {
                "name": name,
                "type_id": type_id,
                "currency": "RUB",
                "description": description,
                "status": 1 if s(variant.get("status")).upper() == "PUBLISHED" else 0,
                # Intentionally omit categories: new cards must remain uncategorized.
                # Supply one SKU row because Shop-Script product.add expects SKUs.
                "skus": [{
                    "price": money_str(desired["price"]),
                    "purchase_price": money_str(desired["purchase_price"]),
                    "compare_price": money_str(desired["compare_price"]),
                    "stock": {wa_stock_id: str(desired["stock"])},
                    "available": 1,
                    "status": 1,
                }],
            }
            created = wa.call("shop.product.add", http_method="POST", data=add_payload)
            product_id = extract_product_id(created)
            if not product_id:
                raise RuntimeError(f"shop.product.add did not return product id: {str(created)[:300]}")

            # Set the exact KIT characteristic "Артикул" as Webasyst SKU immediately.
            skus = get_product_skus(wa, product_id)
            if len(skus) == 1:
                if allow_write():
                    wa.call(
                        "shop.product.skus.update",
                        http_method="POST",
                        params={"id": s(skus[0].get("id"))},
                        data={
                            "sku": article,
                            "price": money_str(desired["price"]),
                            "purchase_price": money_str(desired["purchase_price"]),
                            "compare_price": money_str(desired["compare_price"]),
                            "stock": {wa_stock_id: str(desired["stock"])},
                        },
                    )
            elif len(skus) == 0:
                if allow_write():
                    wa.call(
                        "shop.product.skus.add",
                        http_method="POST",
                        params={"product_id": product_id},
                        data={
                            "sku": article,
                            "price": money_str(desired["price"]),
                            "purchase_price": money_str(desired["purchase_price"]),
                            "compare_price": money_str(desired["compare_price"]),
                            "stock": {wa_stock_id: str(desired["stock"])},
                            "available": 1,
                            "status": 1,
                        },
                    )
            else:
                raise RuntimeError(f"New product unexpectedly has {len(skus)} SKUs")

            # Features are only written on newly-created cards.
            if features and allow_write():
                try:
                    wa.call(
                        "shop.product.update",
                        http_method="POST",
                        params={"id": product_id},
                        data={"features": features},
                    )
                    report["feature_values_written"] += len(features)
                except Exception as exc:
                    report["new_partial"] += 1
                    if len(report["errors"]) < 300:
                        report["errors"].append({
                            "stage": "new_features",
                            "article": article,
                            "product_id": product_id,
                            "message": str(exc)[:500],
                        })

            if pictures:
                upload_images(wa, product_id, pictures, report)

            report["new_created"] += 1
        except Exception as exc:
            if product_id:
                report["new_partial"] += 1
            if len(report["errors"]) < 300:
                report["errors"].append({
                    "stage": "create_new",
                    "article": article,
                    "product_id": product_id,
                    "message": str(exc)[:500],
                })

    # Safety accounting: every write target originates in type 79 or a newly-created type-79 product.
    report["finished_at"] = now_iso()
    report["status"] = "dry-run" if DRY_RUN else ("ok" if not report["errors"] else "degraded")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
