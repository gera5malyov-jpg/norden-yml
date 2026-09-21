#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
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

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from client import WebasystClient

FEED_URL = "https://kennermebel.ru/marketplace/439884.xml"
TYPE_NAME = "kennermebel-334"
EXPECTED_TYPE_ID = "203"
STOCK_NAME = "Основной склад"
EXPECTED_STOCK_ID = "66"
SKU_PREFIX = "334-"
REPORT = HERE / "last_kenner_webasyst_sync.json"
WRITE_DELAY = float(os.getenv("KENNER_WEBASYST_WRITE_DELAY", "0.55"))
MAX_ITEMS = int(os.getenv("MAX_ITEMS", "0") or "0")
MONEY = Decimal("0.01")


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


def as_int(v):
    try:
        return max(0, int(Decimal(str(v or 0))))
    except Exception:
        return 0


def now_iso():
    return datetime.now(timezone.utc).isoformat()


TRANSLIT = str.maketrans({
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z","и":"i","й":"y",
    "к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f",
    "х":"h","ц":"ts","ч":"ch","ш":"sh","щ":"sch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya",
})

def product_url(name, sku):
    text = unicodedata.normalize("NFKC", s(name)).casefold().translate(TRANSLIT)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    sku_part = re.sub(r"[^a-z0-9]+", "-", s(sku).casefold()).strip("-")
    base = text[:140].strip("-") or "kenner"
    return (base + "-" + sku_part).strip("-")


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


def exact_one(rows, wanted, *, label):
    matches = [
        row for row in rows
        if norm(row.get("name") or row.get("title")) == norm(wanted)
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one {label} {wanted!r}; found {len(matches)}")
    return matches[0]


def product_skus(product):
    skus = product.get("skus")
    if isinstance(skus, dict):
        return [x for x in skus.values() if isinstance(x, dict)]
    if isinstance(skus, list):
        return [x for x in skus if isinstance(x, dict)]
    return []


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
        total = payload.get("count") if isinstance(payload, dict) else None
        out.extend(batch)
        if not batch or len(batch) < 1000:
            break
        if total not in (None, "") and len(out) >= int(total):
            break
        offset += len(batch)
    return out


def tag(node):
    return node.tag.split("}")[-1]


def child_text(node, wanted, default=""):
    for child in list(node):
        if tag(child) == wanted:
            return s(child.text)
    return default


def child_texts(node, wanted):
    return [
        s(child.text)
        for child in list(node)
        if tag(child) == wanted and s(child.text)
    ]


def parse_feed():
    response = requests.get(
        FEED_URL,
        timeout=120,
        headers={"User-Agent": "Mozilla/5.0 Kenner-Webasyst-Sync"},
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    rows = []

    for node in root.iter():
        if tag(node) != "offer":
            continue

        supplier_article = (
            child_text(node, "vendorCode")
            or child_text(node, "sku")
            or s(node.attrib.get("id"))
        )
        if not supplier_article:
            continue

        params = []
        seen = set()
        for child in list(node):
            if tag(child) != "param":
                continue
            title = s(child.attrib.get("name"))
            value = s(child.text)
            if not title or not value:
                continue
            key = (norm(title), value)
            if key in seen:
                continue
            seen.add(key)
            params.append((title, value))

        rows.append({
            "source_id": s(node.attrib.get("id")),
            "supplier_article": supplier_article,
            "sku": SKU_PREFIX + supplier_article,
            "name": child_text(node, "name", supplier_article),
            "description": child_text(node, "description"),
            "price": child_text(node, "price"),
            "oldprice": child_text(node, "oldprice"),
            "stock": child_text(node, "stock", "0"),
            "vendor": child_text(node, "vendor", "Kenner") or "Kenner",
            "country": child_text(node, "country_of_origin"),
            "assembly": child_text(node, "assembly"),
            "weight": child_text(node, "weight"),
            "source_url": child_text(node, "url"),
            "pictures": list(dict.fromkeys(child_texts(node, "picture"))),
            "cartons": child_text(node, "cartons"),
            "cartons_weight": child_texts(node, "cartons_weight"),
            "cartons_volume": child_texts(node, "cartons_volume"),
            "cartons_dimensions": child_texts(node, "cartons_dimensions"),
            "params": params,
        })
        node.clear()

    return rows


def extimg_summary(urls):
    urls = [s(x) for x in urls if s(x)]
    if not urls:
        return ""
    return "[extimg]\n" + "\n".join(urls) + "\n[/extimg]"


def source_features(item):
    out = []
    seen = set()

    def add(title, value):
        title = s(title)
        value = s(value)
        key = norm(title)
        if not title or not value or key in seen:
            return
        seen.add(key)
        out.append((title, value))

    add("Webasyst", "334")
    add("Артикул", item["sku"])
    add("Артикул поставщика", item["supplier_article"])
    add("Бренд", item["vendor"])
    add("Страна производства", item["country"])
    add("Вес", item["weight"])
    add("Требуется сборка", item["assembly"])
    add("Количество упаковок", item["cartons"])
    if item["cartons_weight"]:
        add("Вес упаковок", " / ".join(item["cartons_weight"]))
    if item["cartons_volume"]:
        add("Объем упаковок", " / ".join(item["cartons_volume"]))
    if item["cartons_dimensions"]:
        add("Габариты упаковок", " / ".join(item["cartons_dimensions"]))

    for title, value in item["params"]:
        add(title, value)

    return out


def feature_code(title):
    return "kenner_" + hashlib.sha1(norm(title).encode("utf-8")).hexdigest()[:12]


def main():
    started = now_iso()
    wa = WebasystClient(min_request_interval=WRITE_DELAY)
    report = {
        "started_at": started,
        "feed_url": FEED_URL,
        "type_name": TYPE_NAME,
        "type_id": None,
        "stock_name": STOCK_NAME,
        "stock_id": None,
        "source_products": 0,
        "processed": 0,
        "existing_products": 0,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "features_created": 0,
        "feature_values_written": 0,
        "duplicate_sku_groups": 0,
        "errors": [],
        "sample": [],
        "complete": False,
    }

    types = listify(wa.call("shop.type.getList"))
    target_type = exact_one(types, TYPE_NAME, label="Webasyst product type")
    type_id = s(target_type.get("id"))
    if type_id != EXPECTED_TYPE_ID:
        raise RuntimeError(
            f"Safety stop: type {TYPE_NAME!r} changed id from {EXPECTED_TYPE_ID} to {type_id}"
        )
    report["type_id"] = type_id

    stocks = listify(wa.call("shop.stock.getList"))
    target_stock = exact_one(stocks, STOCK_NAME, label="Webasyst stock")
    stock_id = s(target_stock.get("id"))
    if stock_id != EXPECTED_STOCK_ID:
        raise RuntimeError(
            f"Safety stop: stock {STOCK_NAME!r} changed id from {EXPECTED_STOCK_ID} to {stock_id}"
        )
    report["stock_id"] = stock_id

    offers = parse_feed()
    if MAX_ITEMS:
        offers = offers[:MAX_ITEMS]
    report["source_products"] = len(offers)

    products = load_wa_products(wa, type_id)
    report["existing_products"] = len(products)
    by_sku = defaultdict(list)
    for product in products:
        for sku_row in product_skus(product):
            sku = s(sku_row.get("sku"))
            if sku.startswith(SKU_PREFIX):
                by_sku[sku].append((product, sku_row))

    report["duplicate_sku_groups"] = sum(1 for rows in by_sku.values() if len(rows) > 1)

    # Feature lookup: prefer features already attached to type 203; fall back to
    # an exact global feature title; create a dedicated text feature when absent.
    type_features = listify(
        wa.call("shop.feature.getList", params={"type_id": type_id}),
        ("features", "items"),
    )
    all_features = listify(wa.call("shop.feature.getList"), ("features", "items"))
    type_by_title = defaultdict(list)
    global_by_title = defaultdict(list)
    for row in type_features:
        type_by_title[norm(row.get("name") or row.get("title"))].append(row)
    for row in all_features:
        global_by_title[norm(row.get("name") or row.get("title"))].append(row)

    feature_cache = {}

    def usable_feature(rows):
        rows = [
            row for row in rows
            if s(row.get("code"))
            and not bool(int(row.get("selectable") or 0))
            and s(row.get("type")).lower() in ("", "varchar", "text")
        ]
        return rows[0] if len(rows) == 1 else None

    def ensure_feature(title):
        key = norm(title)
        if key in feature_cache:
            return feature_cache[key]

        row = usable_feature(type_by_title.get(key, []))
        if row is None:
            row = usable_feature(global_by_title.get(key, []))

        if row is None:
            code = feature_code(title)
            created = wa.call(
                "shop.feature.add",
                http_method="POST",
                data={
                    "code": code,
                    "type": "varchar",
                    "name": title,
                    "selectable": 0,
                    "multiple": 0,
                    "available_for_sku": 0,
                },
            )
            row = created if isinstance(created, dict) else {}
            if not s(row.get("code")):
                row = {
                    "code": code,
                    "name": title,
                    "type": "varchar",
                    "selectable": 0,
                }
            all_features.append(row)
            global_by_title[key].append(row)
            report["features_created"] += 1

        code = s(row.get("code"))
        if not code:
            raise RuntimeError(f"Feature {title!r} has no code")
        feature_cache[key] = code
        return code

    for item in offers:
        sku = item["sku"]
        try:
            matches = by_sku.get(sku, [])
            if len(matches) > 1:
                raise RuntimeError(f"duplicate Webasyst SKU in type {type_id}: {sku}")

            feature_payload = {}
            for title, value in source_features(item):
                code = ensure_feature(title)
                feature_payload[code] = value

            sale = money(item["price"])
            old = money(item["oldprice"])
            if sale is None or sale <= 0:
                raise RuntimeError(f"invalid Kenner price: {item['price']!r}")
            if old is None or old < sale:
                old = Decimal("0.00")
            qty = as_int(item["stock"])
            summary = extimg_summary(item["pictures"])
            readable_url = product_url(item["name"], sku)

            desired_sku = {
                "price": money_str(sale),
                "compare_price": money_str(old),
                "stock": {stock_id: str(qty)},
                "available": 1,
                "status": 1,
            }

            if matches:
                product, sku_row = matches[0]
                sku_id = s(sku_row.get("id"))

                # Daily mode for existing Kenner products:
                # update ONLY current price, compare price and Main warehouse stock.
                # Product content/characteristics/summary/URL remain untouched after
                # the successful initial import.
                changes = {}
                if money(sku_row.get("price")) != sale:
                    changes["price"] = money_str(sale)
                if money(sku_row.get("compare_price")) != old:
                    changes["compare_price"] = money_str(old)
                current_stock = wa_stock_qty(sku_row, stock_id)
                if current_stock is None or current_stock != qty:
                    changes["stock"] = {stock_id: str(qty)}

                if changes:
                    wa.call(
                        "shop.product.skus.update",
                        http_method="POST",
                        params={"id": sku_id},
                        data=changes,
                    )
                    report["updated"] += 1
                else:
                    report["unchanged"] += 1
            else:
                created = wa.call(
                    "shop.product.add",
                    http_method="POST",
                    data={
                        "name": item["name"],
                        "url": readable_url,
                        "type_id": type_id,
                        "currency": "RUB",
                        "summary": summary,
                        "description": item["description"],
                        "status": 1,
                        # categories intentionally omitted: uncategorized product
                        "features": feature_payload,
                        "skus": [{
                            **desired_sku,
                        }],
                    },
                )
                product_id = extract_product_id(created)
                if not product_id:
                    raise RuntimeError(f"shop.product.add did not return product id: {str(created)[:300]}")

                skus = get_product_skus(wa, product_id)
                if len(skus) != 1:
                    raise RuntimeError(f"new product {product_id} has {len(skus)} SKUs, expected 1")

                wa.call(
                    "shop.product.skus.update",
                    http_method="POST",
                    params={"id": s(skus[0].get("id"))},
                    data={
                        "sku": sku,
                        **desired_sku,
                    },
                )
                report["created"] += 1
                report["feature_values_written"] += len(feature_payload)
                by_sku[sku].append(({"id": product_id, "name": item["name"]}, skus[0]))

            report["processed"] += 1
            if len(report["sample"]) < 15:
                report["sample"].append({
                    "sku": sku,
                    "name": item["name"],
                    "url": readable_url,
                    "stock": qty,
                    "price": money_str(sale),
                    "compare_price": money_str(old),
                    "features": len(feature_payload),
                    "pictures_in_summary": len(item["pictures"]),
                })

        except Exception as exc:
            report["errors"].append({
                "sku": sku,
                "message": str(exc)[:1000],
            })

    report["finished_at"] = now_iso()
    report["status"] = "ok" if not report["errors"] else "degraded"
    report["complete"] = (
        report["processed"] == report["source_products"]
        and not report["errors"]
    )
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
