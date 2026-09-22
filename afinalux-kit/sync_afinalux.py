#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webasyst"))
from client import WebasystClient

FEED_URL = "https://afinalux.ru/index.php?route=feed/yandex_yml"
BRAND = "Afina Garden"
WEBASYST_TYPE = "afinalux"
WEBASYST_STOCK = "Основной склад"
REPORT_PATH = Path(__file__).resolve().parent / "last_sync_report.json"
KIT_API = "https://api.kit.yandex.net"


def s(v):
    return str(v or "").strip()


def norm(v):
    text = unicodedata.normalize("NFKC", s(v)).casefold()
    # Унифицируем визуально одинаковые символы в артикулах/размерах.
    text = text.replace("х", "x").replace("×", "x")
    return re.sub(r"[^0-9a-zа-яё]+", "", text)


def money(v):
    if v in (None, ""):
        return None
    try:
        d = Decimal(str(v).replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d >= 0 else None


def ruble_up(v):
    return Decimal(v).quantize(Decimal("1"), rounding=ROUND_CEILING)


def money_str(v):
    return str(ruble_up(v))


def as_int(v):
    try:
        return max(0, int(Decimal(str(v or 0).replace(",", "."))))
    except Exception:
        return 0


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def child_text(node, tag):
    wanted = tag.casefold()
    for child in list(node):
        if str(child.tag).split("}")[-1].casefold() == wanted:
            return s(child.text)
    return ""


def load_feed():
    r = requests.get(
        FEED_URL,
        timeout=120,
        headers={"User-Agent": "Mozilla/5.0 Afina-Garden-KIT-Webasyst-Sync/1.0"},
    )
    r.raise_for_status()
    root = ET.fromstring(r.content)

    items = {}
    duplicates = defaultdict(list)
    raw_offers = 0
    non_brand = 0
    invalid_purchase = []

    for offer in root.iter():
        if str(offer.tag).split("}")[-1].casefold() != "offer":
            continue
        raw_offers += 1
        vendor = child_text(offer, "vendor")
        if norm(vendor) != norm(BRAND):
            non_brand += 1
            continue

        code = child_text(offer, "vendorCode")
        if not code:
            continue

        purchase = money(child_text(offer, "opt1mln"))
        source_retail = money(child_text(offer, "price"))
        source_oldprice = money(child_text(offer, "oldprice"))
        stock = as_int(child_text(offer, "stock"))

        if purchase is None or purchase <= 0:
            invalid_purchase.append(code)
            continue

        customer_price = ruble_up(purchase * Decimal("1.30"))
        compare_price = ruble_up(purchase * Decimal("1.60"))

        item = {
            "vendor_code": code,
            "name": child_text(offer, "name"),
            "purchase": purchase,
            "customer_price": customer_price,
            "compare_price": compare_price,
            "source_retail": source_retail,
            "source_oldprice": source_oldprice,
            "stock": stock,
            "available": s(offer.attrib.get("available")).casefold() not in {"false", "0", "no"},
            "url": child_text(offer, "url"),
        }

        key = norm(code)
        if key in items:
            duplicates[key].append(items[key])
            duplicates[key].append(item)
        else:
            items[key] = item

    for key in duplicates:
        items.pop(key, None)

    return items, duplicates, raw_offers, non_brand, invalid_purchase


class KitClient:
    def __init__(self, token):
        self.token = s(token)
        if not self.token:
            raise RuntimeError("YANDEX_KIT_TOKEN is not configured")
        self.session = requests.Session()
        self.last_request_at = 0.0

    def request(self, method, path, *, params=None, body=None, timeout=120):
        url = path if path.startswith("http") else KIT_API + path
        for attempt in range(12):
            delay = 0.50 - (time.monotonic() - self.last_request_at)
            if delay > 0:
                time.sleep(delay)
            self.last_request_at = time.monotonic()
            headers = {
                "Authorization": "Bearer " + self.token,
                "Accept": "application/json",
                "User-Agent": "Afina-Garden-Sync/1.0",
            }
            try:
                r = self.session.request(
                    method, url, params=params, json=body, headers=headers, timeout=timeout
                )
            except requests.RequestException:
                if attempt == 11:
                    raise
                time.sleep(min(10, attempt + 1))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == 11:
                    r.raise_for_status()
                time.sleep(float(r.headers.get("Retry-After") or min(12, 2 + attempt)))
                continue
            r.raise_for_status()
            return r.json() if r.content else {}
        raise RuntimeError("KIT request retries exhausted")

    def list_all(self, path, params=None, preferred_key=None):
        rows, page = [], 1
        while True:
            query = dict(params or {})
            query.update({"page": page, "per_page": 100})
            payload = self.request("GET", path, params=query)
            batch = payload.get(preferred_key) if preferred_key else None
            if not isinstance(batch, list):
                batch = []
                for key in ("variants", "characteristics", "items", "results", "data"):
                    if isinstance(payload.get(key), list):
                        batch = payload[key]
                        break
            rows.extend(x for x in batch if isinstance(x, dict))
            total = payload.get("total_count") or payload.get("total")
            if not batch or len(batch) < 100 or (
                total is not None and len(rows) >= int(total)
            ):
                break
            page += 1
        return rows

    def get_variant(self, variant_id):
        return self.request("GET", f"/v1/variants/{variant_id}")

    def update_prices(self, items):
        for start in range(0, len(items), 5000):
            self.request(
                "POST",
                "/v1/variants/prices/bulk_update",
                body={"items": items[start:start + 5000]},
                timeout=180,
            )


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
    if payload and all(isinstance(v, dict) for v in payload.values()):
        return list(payload.values())
    return []


def exact_one(rows, wanted, label):
    matches = [
        row for row in rows
        if norm(row.get("name") or row.get("title")) == norm(wanted)
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Ожидался ровно один {label} {wanted!r}; найдено {len(matches)}"
        )
    return matches[0]


def char_values(variant, char_titles):
    result = []
    wanted_titles = {
        norm("Артикул поставщика"),
        norm("Артикул"),
        norm("Код для сайта"),
        norm("Модель"),
    }
    for row in variant.get("characteristics") or []:
        cid = s(row.get("characteristic_id"))
        title = norm(row.get("title") or char_titles.get(cid))
        if title not in wanted_titles:
            continue
        vals = []
        if row.get("value") not in (None, ""):
            vals.append(row.get("value"))
        vals.extend(row.get("values") or [])
        result.extend(s(v) for v in vals if s(v))
    return result


def candidate_feed_keys(variant, feed_items, char_titles):
    keys = []
    source_keys = set(feed_items)

    for value in [variant.get("sku"), *char_values(variant, char_titles)]:
        key = norm(value)
        if key in source_keys:
            keys.append(key)

    if keys:
        return list(dict.fromkeys(keys))

    name_key = norm(variant.get("name"))
    if name_key:
        for key in source_keys:
            if len(key) >= 4 and key in name_key:
                keys.append(key)

    return list(dict.fromkeys(keys))


def build_kit_index(kit, feed_items):
    chars = kit.list_all(
        "/v1/characteristics", {"status": "ACTIVE"}, "characteristics"
    )
    char_titles = {
        s(x.get("id")): s(x.get("title"))
        for x in chars
        if s(x.get("id"))
    }

    # Параметр brand в KIT ненадёжен, а поиск name не ищет по бренду.
    # Поэтому делаем полный безопасный проход по вариантам и уже локально
    # оставляем только точный бренд Afina Garden.
    rows = kit.list_all("/v1/variants", {}, "variants")
    branded = [
        row for row in rows
        if norm(row.get("brand")) == norm(BRAND)
        and s(row.get("status")).upper() != "ARCHIVED"
    ]

    by_key = {}
    ambiguous = defaultdict(list)
    detailed_reads = 0

    for row in branded:
        vid = s(row.get("id"))
        if not vid:
            continue
        variant = row
        keys = candidate_feed_keys(variant, feed_items, char_titles)

        # Если в списочной выдаче нет характеристик и по SKU/названию
        # сопоставить не удалось, читаем детальную карточку.
        if not keys and not (variant.get("characteristics") or []):
            try:
                variant = kit.get_variant(vid)
                detailed_reads += 1
                keys = candidate_feed_keys(variant, feed_items, char_titles)
            except Exception:
                keys = []

        if len(keys) != 1:
            if len(keys) > 1:
                ambiguous["variant:" + vid].extend(keys)
            continue

        key = keys[0]
        if key in by_key and s(by_key[key].get("id")) != vid:
            ambiguous[key].append(s(by_key[key].get("id")))
            ambiguous[key].append(vid)
            by_key.pop(key, None)
        elif key not in ambiguous:
            by_key[key] = variant

    return by_key, ambiguous, len(branded), detailed_reads

def product_skus(product):
    value = product.get("skus")
    if isinstance(value, dict):
        return [x for x in value.values() if isinstance(x, dict)]
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    return []


def get_product_skus(wa, product_id):
    payload = wa.call("shop.product.skus.getList", params={"product_id": product_id})
    return listify(payload, ("skus", "items"))


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


def build_wa_index(wa, products, feed_items):
    source_keys = set(feed_items)
    by_key = {}
    ambiguous = defaultdict(list)
    fallback_matches = 0

    for product in products:
        pid = s(product.get("id"))
        skus = product_skus(product) or get_product_skus(wa, pid)
        direct = []
        for sku in skus:
            key = norm(sku.get("sku"))
            if key in source_keys:
                direct.append((key, sku))

        if len(direct) == 1:
            key, sku = direct[0]
        elif len(direct) > 1:
            ambiguous["product:" + pid].extend(x[0] for x in direct)
            continue
        else:
            name_key = norm(product.get("name"))
            matches = [
                key for key in source_keys
                if len(key) >= 4 and key in name_key
            ]
            if len(matches) != 1 or len(skus) != 1:
                if len(matches) > 1:
                    ambiguous["product:" + pid].extend(matches)
                continue
            key, sku = matches[0], skus[0]
            fallback_matches += 1

        if key in by_key and s(by_key[key][0].get("id")) != pid:
            ambiguous[key].append(s(by_key[key][0].get("id")))
            ambiguous[key].append(pid)
            by_key.pop(key, None)
        elif key not in ambiguous:
            by_key[key] = (product, sku)

    return by_key, ambiguous, fallback_matches


def run(args):
    report = {
        "started_at": now_iso(),
        "status": "running",
        "dry_run": bool(args.dry_run),
        "feed_url": FEED_URL,
        "brand_expected": BRAND,
        "webasyst_type": WEBASYST_TYPE,
        "webasyst_stock": WEBASYST_STOCK,
        "rules": {
            "purchase_price": "YML <opt1mln>",
            "customer_price": "закупочная цена × 1.30, округление вверх до 1 ₽",
            "compare_price": "закупочная цена × 1.60, округление вверх до 1 ₽",
            "webasyst_main_stock": "точный YML <stock>",
            "creation": "новые товары не создаются ни в KIT, ни в Webasyst",
            "purchase_price_storage": "закупочная цена отдельным полем не записывается",
        },
        "errors": [],
        "warnings": [],
    }

    feed_items, feed_duplicates, raw_offers, non_brand, invalid_purchase = load_feed()
    report.update({
        "feed_offers_total": raw_offers,
        "feed_brand_items_unique": len(feed_items),
        "feed_non_brand_offers": non_brand,
        "feed_invalid_purchase": invalid_purchase[:100],
        "feed_duplicate_vendor_codes": [
            rows[0]["vendor_code"] for rows in feed_duplicates.values() if rows
        ][:100],
        "feed_zero_stock": sum(1 for x in feed_items.values() if x["stock"] == 0),
    })

    if len(feed_items) < 100:
        raise RuntimeError(
            f"Safety stop: expected a substantial Afina Garden feed, got only {len(feed_items)} unique items"
        )

    kit = KitClient(os.getenv("YANDEX_KIT_TOKEN"))
    kit_index, kit_ambiguous, kit_brand_count, detail_reads = build_kit_index(
        kit, feed_items
    )
    report.update({
        "kit_afina_active_variants": kit_brand_count,
        "kit_matched": len(kit_index),
        "kit_detailed_reads": detail_reads,
        "kit_ambiguous": dict(list(kit_ambiguous.items())[:100]),
        "kit_unmatched_vendor_codes": [
            feed_items[k]["vendor_code"]
            for k in sorted(set(feed_items) - set(kit_index))
        ][:100],
    })

    if not kit_index:
        raise RuntimeError(
            "Safety stop: no exact Afina Garden matches found in KIT"
        )

    wa = WebasystClient(min_request_interval=float(
        os.getenv("AFINALUX_WEBASYST_WRITE_DELAY", "0.55")
    ))
    types = listify(wa.call("shop.type.getList"))
    target_type = exact_one(types, WEBASYST_TYPE, "тип товара Webasyst")
    type_id = s(target_type.get("id"))

    stocks = listify(wa.call("shop.stock.getList"))
    target_stock = exact_one(stocks, WEBASYST_STOCK, "склад Webasyst")
    stock_id = s(target_stock.get("id"))

    wa_products = load_wa_products(wa, type_id)
    wa_index, wa_ambiguous, wa_fallback = build_wa_index(
        wa, wa_products, feed_items
    )
    report.update({
        "webasyst_type_id": type_id,
        "webasyst_stock_id": stock_id,
        "webasyst_products_in_type": len(wa_products),
        "webasyst_matched": len(wa_index),
        "webasyst_name_fallback_matches": wa_fallback,
        "webasyst_ambiguous": dict(list(wa_ambiguous.items())[:100]),
        "webasyst_unmatched_vendor_codes": [
            feed_items[k]["vendor_code"]
            for k in sorted(set(feed_items) - set(wa_index))
        ][:100],
    })

    kit_updates = []
    for key, variant in sorted(kit_index.items()):
        item = feed_items[key]
        kit_updates.append({
            "variant_id": s(variant.get("id")),
            "price": money_str(item["compare_price"]),
            "manual_discount_price": money_str(item["customer_price"]),
        })

    report["kit_price_updates_planned"] = len(kit_updates)
    if not args.dry_run and kit_updates:
        kit.update_prices(kit_updates)
    report["kit_price_updates_sent"] = 0 if args.dry_run else len(kit_updates)

    wa_updates = 0
    wa_samples = []
    for key, (product, sku) in sorted(wa_index.items()):
        item = feed_items[key]
        try:
            payload = {
                "price": money_str(item["customer_price"]),
                "compare_price": money_str(item["compare_price"]),
                "stock": {stock_id: str(item["stock"])},
                "available": 1 if item["stock"] > 0 else 0,
                "status": 1,
            }
            if not args.dry_run:
                wa.call(
                    "shop.product.skus.update",
                    http_method="POST",
                    params={"id": s(sku.get("id"))},
                    data=payload,
                )
            wa_updates += 1
            if len(wa_samples) < 30:
                wa_samples.append({
                    "vendor_code": item["vendor_code"],
                    "product_id": s(product.get("id")),
                    "sku_id": s(sku.get("id")),
                    "purchase": money_str(item["purchase"]),
                    "customer_price": money_str(item["customer_price"]),
                    "compare_price": money_str(item["compare_price"]),
                    "stock": item["stock"],
                })
        except Exception as exc:
            report["errors"].append({
                "stage": "webasyst_update",
                "vendor_code": item["vendor_code"],
                "product_id": s(product.get("id")),
                "message": str(exc)[:1000],
            })

    report["webasyst_updates_planned"] = wa_updates
    report["webasyst_updates_sent"] = 0 if args.dry_run else wa_updates
    report["samples"] = wa_samples
    report["finished_at"] = now_iso()
    report["status"] = "ok" if not report["errors"] else "degraded"
    report["complete"] = not report["errors"]

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["complete"] else 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    try:
        return run(args)
    except Exception as exc:
        report = {
            "started_at": now_iso(),
            "finished_at": now_iso(),
            "status": "error",
            "dry_run": bool(args.dry_run),
            "feed_url": FEED_URL,
            "brand_expected": BRAND,
            "error": str(exc)[:2000],
        }
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
