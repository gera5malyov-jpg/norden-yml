#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import mimetypes
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlparse

import requests

FEED_URL = "https://market-b2bfabrika.ru/bitrix/catalog_export/catalog-feed.xml"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PRICE_PATH = HERE / "prices.json"
MAPPING_PATH = HERE / "mapping.json"
REPORT_PATH = HERE / "last_sync_report.json"

BRAND = "Б2Б Фабрика"
WEBASYST_VALUE = "335"
SKU_PREFIX = "335-"
WAREHOUSE_NAMES = ("МСК", "СПБ привозной")
MONEY = Decimal("0.01")


def s(v):
    return str(v or "").strip()


def norm(v):
    return re.sub(r"[^0-9a-zа-яё]+", "", unicodedata.normalize("NFKC", s(v)).casefold())


def dec(v):
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None


def money(v):
    d = dec(v)
    return None if d is None else d.quantize(MONEY, rounding=ROUND_HALF_UP)


def price_set(purchase):
    p = money(purchase)
    if p is None or p <= 0:
        return None
    return {
        "purchase": p,
        "sale": money(p * Decimal("1.30")),
        "old": money(p * Decimal("1.80")),
        "minimum": money(p * Decimal("1.20")),
    }


def first_text(node, tag, default=""):
    for child in list(node):
        if child.tag.split("}")[-1].casefold() == tag.casefold():
            return s(child.text)
    return default


def all_text(node, tag):
    return [s(x.text) for x in list(node) if x.tag.split("}")[-1].casefold() == tag.casefold() and s(x.text)]


def load_prices():
    raw = json.loads(PRICE_PATH.read_text(encoding="utf-8"))
    return {s(k).casefold(): v for k, v in raw.items()}


def parse_count(node):
    for tag in ("count", "quantity", "stock"):
        value = first_text(node, tag)
        if value:
            try:
                return max(0, int(Decimal(value.replace(" ", "").replace(",", "."))))
            except Exception:
                pass
    for child in list(node):
        if child.tag.split("}")[-1].casefold() != "param":
            continue
        name = norm(child.attrib.get("name"))
        if name in ("count", "остаток", "количество"):
            try:
                return max(0, int(Decimal(s(child.text).replace(" ", "").replace(",", "."))))
            except Exception:
                return 0
    return 0


def parse_feed(prices):
    r = requests.get(FEED_URL, timeout=180, headers={"User-Agent": "Mozilla/5.0 B2B-335-KIT"})
    r.raise_for_status()
    root = ET.fromstring(r.content)

    categories = {}
    for n in root.iter():
        if n.tag.split("}")[-1] != "category":
            continue
        cid = s(n.attrib.get("id"))
        if not cid:
            continue
        categories[cid] = {
            "title": s(n.text) or cid,
            "parent": s(n.attrib.get("parentId") or n.attrib.get("parent_id")),
        }

    offers = {}
    duplicates = []
    for n in root.iter():
        if n.tag.split("}")[-1] != "offer":
            continue
        supplier_article = first_text(n, "vendorCode") or first_text(n, "sku") or s(n.attrib.get("id"))
        if not supplier_article:
            continue
        key = supplier_article.casefold()
        params = {}
        for child in list(n):
            if child.tag.split("}")[-1] != "param":
                continue
            title, value = s(child.attrib.get("name")), s(child.text)
            if title and value:
                params.setdefault(title, [])
                if value not in params[title]:
                    params[title].append(value)
        item = {
            "supplier_article": supplier_article,
            "source_id": s(n.attrib.get("id")),
            "name": first_text(n, "name", supplier_article),
            "description": first_text(n, "description"),
            "category_id": first_text(n, "categoryId"),
            "source_url": first_text(n, "url"),
            "pictures": all_text(n, "picture"),
            "params": params,
            "count": parse_count(n),
            "purchase": prices.get(key),
        }
        if key in offers:
            duplicates.append(supplier_article)
            continue
        offers[key] = item
    return categories, offers, duplicates


def load_kit_module():
    path = ROOT / "kenner-kit" / "sync_kenner_kit.py"
    spec = importlib.util.spec_from_file_location("kenner_kit_for_b2b335", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_mapping():
    if not MAPPING_PATH.exists():
        return {"variants": {}}
    try:
        x = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
        return x if isinstance(x, dict) else {"variants": {}}
    except Exception:
        return {"variants": {}}


def save_mapping(mapping):
    MAPPING_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")


def char_value(variant, cid):
    for row in variant.get("characteristics") or []:
        if s(row.get("characteristic_id")) != s(cid):
            continue
        values = row.get("values") or []
        return s(row.get("value")) or (s(values[0]) if values else "")
    return ""


def run():
    token = s(os.getenv("YANDEX_KIT_TOKEN"))
    if not token:
        raise RuntimeError("YANDEX_KIT_TOKEN is not configured")

    prices = load_prices()
    source_categories, offers, feed_duplicates = parse_feed(prices)
    kit_mod = load_kit_module()
    kit = kit_mod.KitClient(token)

    report = {
        "status": "running",
        "feed_url": FEED_URL,
        "source_products": len(offers),
        "price_articles": len(prices),
        "feed_duplicate_articles": feed_duplicates[:100],
        "created": 0,
        "updated": 0,
        "zeroed_missing_from_feed": 0,
        "price_updates": 0,
        "stock_updates": 0,
        "images_uploaded": 0,
        "image_errors": 0,
        "missing_purchase_price": [],
        "minimum_price_field": None,
        "purchase_price_field": None,
        "warnings": [],
        "errors": [],
        "sku_rule": "335-<KIT kit_id>",
        "brand": BRAND,
        "webasyst_characteristic": WEBASYST_VALUE,
        "warehouses": list(WAREHOUSE_NAMES),
        "price_rules": {
            "purchase": "Оптовый прайс: Цена Опт динамика",
            "customer": "purchase * 1.30",
            "before_discount": "purchase * 1.80",
            "minimum": "purchase * 1.20",
        },
    }
    HERE.mkdir(parents=True, exist_ok=True)

    warehouses = kit.list_all("/v1/warehouses", {"status": "ACTIVE"}, "warehouses")
    wh = {s(x.get("title")): s(x.get("id")) for x in warehouses if s(x.get("id"))}
    missing_wh = [x for x in WAREHOUSE_NAMES if x not in wh]
    if missing_wh:
        raise RuntimeError("KIT warehouses not found: " + ", ".join(missing_wh))

    categories = kit.list_all("/v1/categories", {"status": "ACTIVE"}, "categories")

    def ensure_category(title, parent_id=""):
        matches = [
            x for x in categories
            if norm(x.get("title")) == norm(title)
            and s(x.get("parent_id")) == s(parent_id)
        ]
        if matches:
            return s(matches[0].get("id"))
        created = kit.create_category(title, parent_id or None)
        cid = s(created.get("id"))
        if not cid:
            raise RuntimeError(f"No category id for {title!r}")
        row = dict(created)
        row.setdefault("parent_id", parent_id)
        categories.append(row)
        return cid

    cat_cache = {}
    def ensure_source_category(cid):
        cid = s(cid)
        if not cid:
            return ""
        if cid in cat_cache:
            return cat_cache[cid]
        row = source_categories.get(cid)
        if not row:
            return ""
        parent_id = ensure_source_category(row.get("parent"))
        cat_cache[cid] = ensure_category(row["title"], parent_id)
        return cat_cache[cid]

    characteristics = kit.list_all("/v1/characteristics", {"status": "ACTIVE"}, "characteristics")
    char_cache = {}
    def ensure_char(title):
        k = norm(title)
        if k in char_cache:
            return char_cache[k]
        matches = [x for x in characteristics if norm(x.get("title")) == k]
        if matches:
            cid = s(matches[0].get("id"))
        else:
            created = kit.create_characteristic(title)
            cid = s(created.get("id"))
            if not cid:
                raise RuntimeError(f"No characteristic id for {title!r}")
            characteristics.append(created)
        char_cache[k] = cid
        return cid

    webasyst_cid = ensure_char("Webasyst")
    supplier_cid = ensure_char("Артикул поставщика")
    article_cid = ensure_char("Артикул")
    site_code_cid = ensure_char("Код для сайта")
    purchase_char_cid = ensure_char("Закупочная цена")

    mapping = load_mapping()
    mapping.setdefault("variants", {})

    # Recover mapping if state file was lost.
    if not mapping["variants"]:
        try:
            for row in kit.list_all("/v1/variants", {"name": SKU_PREFIX}, "variants"):
                if s(row.get("status")).upper() == "ARCHIVED":
                    continue
                vid = s(row.get("id"))
                if not vid:
                    continue
                full = kit.get_variant(vid)
                if char_value(full, webasyst_cid) != WEBASYST_VALUE:
                    continue
                supplier = char_value(full, supplier_cid)
                if supplier:
                    mapping["variants"][supplier.casefold()] = {
                        "variant_id": vid,
                        "kit_id": full.get("kit_id"),
                        "sku": s(full.get("sku")),
                    }
            save_mapping(mapping)
        except Exception as exc:
            report["warnings"].append("Mapping rebuild warning: " + str(exc)[:500])

    def prepare_media(item):
        media = []
        for url in item["pictures"]:
            try:
                uploaded = kit.upload_image_url(url)
                fid = s(uploaded.get("id"))
                if fid:
                    media.append({"type": "IMAGE", "display_sequence": len(media), "image_id": fid})
                    report["images_uploaded"] += 1
            except Exception as exc:
                report["image_errors"] += 1
                if len(report["warnings"]) < 300:
                    report["warnings"].append(f"{item['supplier_article']}: image {url}: {exc}")
        return media

    def build_chars(item, final_sku):
        pairs = [
            ("Webasyst", WEBASYST_VALUE, webasyst_cid),
            ("Артикул поставщика", item["supplier_article"], supplier_cid),
            ("Артикул", final_sku, article_cid),
            ("Код для сайта", final_sku, site_code_cid),
        ]
        if item.get("purchase") not in (None, ""):
            pairs.append(("Закупочная цена", str(item["purchase"]), purchase_char_cid))
        used = {norm(x[0]) for x in pairs}
        for title, values in item.get("params", {}).items():
            if norm(title) in used:
                continue
            value = " / ".join(values)
            if value:
                pairs.append((title, value, ensure_char(title)))
                used.add(norm(title))
        return [{"characteristic_id": cid, "value": value, "values": [value]} for _, value, cid in pairs]

    price_rows = []
    stock_rows = []

    for key, item in offers.items():
        try:
            mapped = mapping["variants"].get(key)
            ps = price_set(item.get("purchase"))
            if ps is None:
                report["missing_purchase_price"].append(item["supplier_article"])

            if mapped:
                variant_id = s(mapped.get("variant_id"))
                variant = kit.get_variant(variant_id)
                kit_id = variant.get("kit_id") or mapped.get("kit_id")
                if kit_id in (None, ""):
                    raise RuntimeError("Existing variant has no kit_id")
                final_sku = f"{SKU_PREFIX}{kit_id}"
                patch = {}
                if s(variant.get("sku")) != final_sku:
                    patch["sku"] = final_sku
                if s(variant.get("brand")) != BRAND:
                    patch["brand"] = BRAND
                existing = list(variant.get("characteristics") or [])
                by_id = {s(x.get("characteristic_id")): x for x in existing}
                for x in build_chars(item, final_sku):
                    if s(x["characteristic_id"]) not in by_id:
                        existing.append(x)
                patch["characteristics"] = existing
                if patch:
                    kit.patch_variant(variant_id, patch)
                mapping["variants"][key] = {"variant_id": variant_id, "kit_id": kit_id, "sku": final_sku}
                report["updated"] += 1
            else:
                category_id = ensure_source_category(item.get("category_id"))
                if not category_id:
                    # Fallback only if the feed category reference is absent.
                    category_id = ensure_category("Мебель для дома")
                product = kit.create_product(category_id)
                product_id = s(product.get("id"))
                if not product_id:
                    raise RuntimeError("KIT did not return product id")
                safe = re.sub(r"[^0-9A-Za-zА-Яа-я]+", "-", item["supplier_article"])[:40].strip("-") or "ITEM"
                tmp_sku = f"B2B335-TMP-{safe}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]}"
                body = {
                    "sku": tmp_sku,
                    "name": item["name"],
                    "description": item["description"],
                    "status": "PUBLISHED",
                    "product_id": product_id,
                    "brand": BRAND,
                    "stocks": [
                        {"warehouse_id": wh[name], "quantity": int(item["count"]), "reserved": 0}
                        for name in WAREHOUSE_NAMES
                    ],
                }
                if ps:
                    body["pricing"] = {
                        "price": f"{ps['old']:.2f}",
                        "manual_discount_price": f"{ps['sale']:.2f}",
                    }
                media = prepare_media(item)
                if media:
                    body["media"] = media
                created = kit.create_variant(body)
                variant_id = s(created.get("id"))
                if not variant_id:
                    raise RuntimeError("KIT did not return variant id")
                kit_id = created.get("kit_id")
                if kit_id in (None, ""):
                    created = kit.get_variant(variant_id)
                    kit_id = created.get("kit_id")
                if kit_id in (None, ""):
                    raise RuntimeError("KIT did not return kit_id")
                final_sku = f"{SKU_PREFIX}{kit_id}"
                kit.patch_variant(variant_id, {
                    "sku": final_sku,
                    "brand": BRAND,
                    "characteristics": build_chars(item, final_sku),
                })
                mapping["variants"][key] = {"variant_id": variant_id, "kit_id": kit_id, "sku": final_sku}
                save_mapping(mapping)
                report["created"] += 1

            variant_id = s(mapping["variants"][key]["variant_id"])
            if ps:
                price_rows.append({
                    "variant_id": variant_id,
                    "price": f"{ps['old']:.2f}",
                    "manual_discount_price": f"{ps['sale']:.2f}",
                    "_minimum": f"{ps['minimum']:.2f}",
                    "_purchase": f"{ps['purchase']:.2f}",
                })
            for name in WAREHOUSE_NAMES:
                stock_rows.append({"variant_id": variant_id, "warehouse_id": wh[name], "quantity": int(item["count"])})
        except Exception as exc:
            report["errors"].append({"article": item["supplier_article"], "message": str(exc)[:1000]})

    # Products that disappeared from the source are retained, but stock becomes zero.
    source_keys = set(offers)
    for key, mapped in mapping["variants"].items():
        if key in source_keys:
            continue
        vid = s(mapped.get("variant_id"))
        if not vid:
            continue
        for name in WAREHOUSE_NAMES:
            stock_rows.append({"variant_id": vid, "warehouse_id": wh[name], "quantity": 0})
        report["zeroed_missing_from_feed"] += 1

    # Discover optional native minimum/purchase fields without assuming KIT API names.
    def discover_field(candidates, sample, hidden_key):
        if not sample:
            return None
        base = {k: v for k, v in sample.items() if not k.startswith("_")}
        for field in candidates:
            row = dict(base)
            row[field] = sample[hidden_key]
            try:
                kit.request("POST", "/v1/variants/prices/bulk_update", body={"items": [row]})
                return field
            except requests.HTTPError as exc:
                if getattr(exc.response, "status_code", 0) not in (400, 404, 409, 422):
                    raise
        return None

    if price_rows:
        report["minimum_price_field"] = discover_field(
            ("minimum_price", "min_price", "minimum_sale_price", "manual_minimum_price"),
            price_rows[0], "_minimum"
        )
        report["purchase_price_field"] = discover_field(
            ("purchase_price", "cost_price", "procurement_price", "acquisition_price"),
            price_rows[0], "_purchase"
        )

    for start in range(0, len(price_rows), 500):
        batch = []
        for row in price_rows[start:start+500]:
            x = {k: v for k, v in row.items() if not k.startswith("_")}
            if report["minimum_price_field"]:
                x[report["minimum_price_field"]] = row["_minimum"]
            if report["purchase_price_field"]:
                x[report["purchase_price_field"]] = row["_purchase"]
            batch.append(x)
        kit.request("POST", "/v1/variants/prices/bulk_update", body={"items": batch})
        report["price_updates"] += len(batch)

    for start in range(0, len(stock_rows), 1000):
        batch = stock_rows[start:start+1000]
        kit.request("POST", "/v1/variants/stocks/bulk_update", body={"items": batch})
        report["stock_updates"] += len(batch)

    save_mapping(mapping)
    report["mapped_products"] = len(mapping["variants"])
    report["status"] = "ok" if not report["errors"] and not report["missing_purchase_price"] else "degraded"
    report["complete"] = (
        not report["errors"]
        and not report["missing_purchase_price"]
        and len(mapping["variants"]) >= len(offers)
    )
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(run())
