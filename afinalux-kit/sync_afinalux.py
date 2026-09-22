#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webasyst"))
from client import WebasystClient

FEED_URL = "https://afinalux.ru/index.php?route=feed/yandex_yml"
BRAND = "Afina Garden"
WEBASYST_TYPE = "afinalux"
WEBASYST_STOCK = "Основной склад"
KIT_ROOT_CATEGORY = "Afina Garden"
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


def all_child_text(node, tag):
    wanted = tag.casefold()
    out = []
    for child in list(node):
        if str(child.tag).split("}")[-1].casefold() == wanted:
            value = s(child.text)
            if value and value not in out:
                out.append(value)
    return out


def load_feed():
    r = requests.get(
        FEED_URL,
        timeout=120,
        headers={"User-Agent": "Mozilla/5.0 Afina-Garden-KIT-Webasyst-Sync/1.0"},
    )
    r.raise_for_status()
    root = ET.fromstring(r.content)

    source_categories = {}
    for node in root.iter():
        if str(node.tag).split("}")[-1].casefold() != "category":
            continue
        cid = s(node.attrib.get("id"))
        title = s(node.text)
        if cid and title:
            source_categories[cid] = title

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
            "name": child_text(offer, "name") or child_text(offer, "model") or code,
            "description": child_text(offer, "description"),
            "category_id": child_text(offer, "categoryId"),
            "pictures": all_child_text(offer, "picture"),
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

    return items, duplicates, raw_offers, non_brand, invalid_purchase, source_categories


class KitClient:
    def __init__(self, token):
        self.token = s(token)
        if not self.token:
            raise RuntimeError("YANDEX_KIT_TOKEN is not configured")
        self.session = requests.Session()
        self.last_request_at = 0.0

    def request(self, method, path, *, params=None, body=None, files=None, timeout=120):
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
                    method, url, params=params, json=body, files=files, headers=headers, timeout=timeout
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

    def create_category(self, title, parent_id=None):
        body = {"title": title}
        if parent_id:
            body["parent_id"] = parent_id
        return self.request("POST", "/v1/categories", body=body)

    def create_characteristic(self, title):
        return self.request(
            "POST",
            "/v1/characteristics",
            body={"title": title, "type": "STRING", "select_mode": "SINGLE"},
        )

    def create_product(self, category_id):
        return self.request("POST", "/v1/products", body={"category_ids": [category_id]})

    def create_variant(self, body):
        return self.request("POST", "/v1/variants", body=body, timeout=180)

    def get_variant(self, variant_id):
        return self.request("GET", f"/v1/variants/{variant_id}")

    def patch_variant(self, variant_id, body):
        return self.request("PATCH", f"/v1/variants/{variant_id}", body=body)

    def upload_image_url(self, url):
        response = requests.get(
            url,
            timeout=120,
            headers={"User-Agent": "Mozilla/5.0 Afina-Garden-Sync"},
        )
        response.raise_for_status()
        content_type = (
            response.headers.get("Content-Type")
            or mimetypes.guess_type(urlparse(url).path)[0]
            or "image/jpeg"
        )
        ext = os.path.splitext(urlparse(url).path)[1] or ".jpg"
        return self.request(
            "POST",
            "/v1/files",
            files={"file": ("afina" + ext[:10], response.content, content_type)},
            timeout=180,
        )

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
    source_keys = set(feed_items)
    direct = []

    # Only strong identifiers: SKU, supplier article, article and site code.
    # Generic model values (e.g. T133) are excluded to avoid false bundle matches.
    for value in [variant.get("sku"), *char_values(variant, char_titles)]:
        key = norm(value)
        if key in source_keys:
            direct.append(key)

    direct = list(dict.fromkeys(direct))
    if len(direct) == 1:
        return direct
    if len(direct) > 1:
        return direct

    name_key = norm(variant.get("name"))
    if not name_key:
        return []

    exact_name = [
        key for key, item in feed_items.items()
        if norm(item.get("name")) == name_key
    ]
    return exact_name if len(exact_name) == 1 else []


def build_kit_index(kit, feed_items):
    chars = kit.list_all(
        "/v1/characteristics", {"status": "ACTIVE"}, "characteristics"
    )
    char_titles = {
        s(x.get("id")): s(x.get("title"))
        for x in chars
        if s(x.get("id"))
    }

    rows = kit.list_all("/v1/variants", {}, "variants")
    active = [
        row for row in rows
        if s(row.get("status")).upper() != "ARCHIVED"
    ]

    source_keys = set(feed_items)
    by_key = {}
    ambiguous = defaultdict(list)
    detailed_reads = 0
    afina_count = 0

    for row in active:
        vid = s(row.get("id"))
        if not vid:
            continue

        row_brand = norm(row.get("brand"))
        row_sku_key = norm(row.get("sku"))
        is_afina = row_brand == norm(BRAND)
        if is_afina:
            afina_count += 1

        # Read every Afina Garden detail card, because list responses often omit
        # characteristics where the supplier article is stored.
        if not is_afina and row_sku_key not in source_keys:
            continue

        variant = row
        try:
            variant = kit.get_variant(vid)
            detailed_reads += 1
        except Exception:
            variant = row

        keys = candidate_feed_keys(variant, feed_items, char_titles)
        if len(keys) != 1:
            if len(keys) > 1:
                ambiguous["variant:" + vid].extend(keys)
            continue

        key = keys[0]
        variant_brand = norm(variant.get("brand"))
        exact_sku = norm(variant.get("sku")) == key

        if variant_brand and variant_brand != norm(BRAND):
            ambiguous[key].append(
                "brand_conflict:" + s(variant.get("brand")) + ":" + vid
            )
            continue
        if not exact_sku and variant_brand != norm(BRAND):
            continue

        if key in by_key and s(by_key[key].get("id")) != vid:
            ambiguous[key].append(s(by_key[key].get("id")))
            ambiguous[key].append(vid)
            by_key.pop(key, None)
        elif key not in ambiguous:
            by_key[key] = variant

    return by_key, ambiguous, afina_count, detailed_reads, chars


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

    feed_names = defaultdict(list)
    for key, item in feed_items.items():
        name_key = norm(item.get("name"))
        if name_key:
            feed_names[name_key].append(key)

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
            if len(skus) != 1:
                continue

            name_key = norm(product.get("name"))
            exact_name = feed_names.get(name_key, [])
            if len(exact_name) == 1:
                key, sku = exact_name[0], skus[0]
                fallback_matches += 1
            else:
                # Last fallback is intentionally conservative. Short codes such
                # as T133 are not used because they occur inside bundle names.
                matches = [
                    k for k in source_keys
                    if len(k) >= 8 and k in name_key
                ]
                if len(matches) != 1:
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
            "creation": "если точного артикула нет — создать новую карточку в KIT и Webasyst",
            "duplicate_guard": "перед созданием повторная точная проверка SKU/артикула",
            "purchase_price_storage": "закупочная цена отдельным полем не записывается",
        },
        "errors": [],
        "warnings": [],
    }

    (
        feed_items,
        feed_duplicates,
        raw_offers,
        non_brand,
        invalid_purchase,
        source_categories,
    ) = load_feed()
    report.update({
        "feed_offers_total": raw_offers,
        "feed_brand_items_unique": len(feed_items),
        "feed_categories": len(source_categories),
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
    (
        kit_index,
        kit_ambiguous,
        kit_brand_count,
        detail_reads,
        kit_characteristics,
    ) = build_kit_index(kit, feed_items)

    report.update({
        "kit_afina_active_variants_before": kit_brand_count,
        "kit_matched_before_creation": len(kit_index),
        "kit_detailed_reads": detail_reads,
        "kit_ambiguous": dict(list(kit_ambiguous.items())[:100]),
        "kit_created": 0,
        "kit_would_create": 0,
        "kit_reused_exact_sku": 0,
        "kit_brand_fixed": 0,
        "kit_images_uploaded": 0,
        "kit_image_errors": 0,
    })

    # Prepare KIT categories/characteristics only when creation may be needed.
    kit_categories = kit.list_all(
        "/v1/categories", {"status": "ACTIVE"}, "categories"
    )
    root_category_id = ""
    source_to_kit_category = {}
    supplier_article_cid = ""
    site_code_cid = ""

    def ensure_creation_metadata():
        nonlocal root_category_id, supplier_article_cid, site_code_cid
        if not root_category_id:
            root_category_id = ensure_kit_category(
                kit, kit_categories, KIT_ROOT_CATEGORY
            )
        if not supplier_article_cid:
            supplier_article_cid = ensure_kit_characteristic(
                kit, kit_characteristics, "Артикул поставщика"
            )
        if not site_code_cid:
            site_code_cid = ensure_kit_characteristic(
                kit, kit_characteristics, "Код для сайта"
            )

    missing_kit_keys = [
        key for key in sorted(feed_items)
        if key not in kit_index and key not in kit_ambiguous
    ]

    for key in missing_kit_keys:
        item = feed_items[key]
        code = item["vendor_code"]
        try:
            exact = exact_kit_sku_rows(kit, code)
            if len(exact) > 1:
                report["errors"].append({
                    "stage": "kit_duplicate_guard",
                    "vendor_code": code,
                    "message": f"Найдено {len(exact)} активных карточек KIT с точным SKU",
                })
                continue

            if len(exact) == 1:
                variant = kit.get_variant(s(exact[0].get("id")))
                existing_brand = s(variant.get("brand"))
                if existing_brand and norm(existing_brand) != norm(BRAND):
                    report["errors"].append({
                        "stage": "kit_brand_conflict",
                        "vendor_code": code,
                        "variant_id": s(variant.get("id")),
                        "message": f"Точный SKU уже занят брендом {existing_brand!r}; новая карточка не создана",
                    })
                    continue
                if not args.dry_run and norm(existing_brand) != norm(BRAND):
                    kit.patch_variant(s(variant.get("id")), {"brand": BRAND})
                    variant["brand"] = BRAND
                    report["kit_brand_fixed"] += 1
                kit_index[key] = variant
                report["kit_reused_exact_sku"] += 1
                continue

            if args.dry_run:
                report["kit_would_create"] += 1
                continue

            ensure_creation_metadata()
            source_cat = s(item.get("category_id"))
            category_id = root_category_id
            if source_cat:
                if source_cat not in source_to_kit_category:
                    title = source_categories.get(source_cat) or source_cat
                    source_to_kit_category[source_cat] = ensure_kit_category(
                        kit, kit_categories, title, root_category_id
                    )
                category_id = source_to_kit_category[source_cat]

            product = kit.create_product(category_id)
            product_id = s(product.get("id"))
            if not product_id:
                raise RuntimeError("KIT did not return product id")

            characteristics = [
                {
                    "characteristic_id": supplier_article_cid,
                    "value": code,
                    "values": [code],
                },
                {
                    "characteristic_id": site_code_cid,
                    "value": code,
                    "values": [code],
                },
            ]

            media = []
            for picture in item.get("pictures") or []:
                if len(media) >= 10:
                    break
                try:
                    uploaded = kit.upload_image_url(picture)
                    image_id = s(uploaded.get("id"))
                    if image_id:
                        media.append({
                            "type": "IMAGE",
                            "display_sequence": len(media),
                            "image_id": image_id,
                        })
                        report["kit_images_uploaded"] += 1
                except Exception as exc:
                    report["kit_image_errors"] += 1
                    if len(report["warnings"]) < 200:
                        report["warnings"].append({
                            "stage": "kit_image",
                            "vendor_code": code,
                            "message": str(exc)[:500],
                        })

            body = {
                "sku": code,
                "name": item["name"],
                "description": item.get("description") or "",
                "status": "PUBLISHED",
                "product_id": product_id,
                "brand": BRAND,
                "characteristics": characteristics,
                "pricing": {
                    "price": money_str(item["compare_price"]),
                    "manual_discount_price": money_str(item["customer_price"]),
                },
            }
            if media:
                body["media"] = media

            created = kit.create_variant(body)
            variant_id = s(created.get("id"))
            if not variant_id:
                raise RuntimeError("KIT did not return variant id")
            created["id"] = variant_id
            created.setdefault("sku", code)
            created.setdefault("brand", BRAND)
            kit_index[key] = created
            report["kit_created"] += 1
        except Exception as exc:
            report["errors"].append({
                "stage": "kit_create",
                "vendor_code": code,
                "message": str(exc)[:1000],
            })

    report["kit_matched_after_creation"] = len(kit_index)
    report["kit_unmatched_after_creation"] = [
        feed_items[k]["vendor_code"]
        for k in sorted(set(feed_items) - set(kit_index))
        if k not in kit_ambiguous
    ][:100]

    # Bring exact existing cards to the correct brand where safe.
    for key, variant in list(kit_index.items()):
        vid = s(variant.get("id"))
        if not vid or args.dry_run:
            continue
        if norm(variant.get("brand")) != norm(BRAND):
            try:
                detail = kit.get_variant(vid)
                current_brand = s(detail.get("brand"))
                if not current_brand or norm(current_brand) == norm(BRAND):
                    if norm(current_brand) != norm(BRAND):
                        kit.patch_variant(vid, {"brand": BRAND})
                        report["kit_brand_fixed"] += 1
                    variant["brand"] = BRAND
            except Exception as exc:
                if len(report["warnings"]) < 200:
                    report["warnings"].append({
                        "stage": "kit_brand_fix",
                        "vendor_code": feed_items[key]["vendor_code"],
                        "message": str(exc)[:500],
                    })

    kit_updates = []
    for key, variant in sorted(kit_index.items()):
        item = feed_items[key]
        vid = s(variant.get("id"))
        if not vid:
            continue
        kit_updates.append({
            "variant_id": vid,
            "price": money_str(item["compare_price"]),
            "manual_discount_price": money_str(item["customer_price"]),
        })

    report["kit_price_updates_planned"] = len(kit_updates)
    if not args.dry_run and kit_updates:
        kit.update_prices(kit_updates)
    report["kit_price_updates_sent"] = 0 if args.dry_run else len(kit_updates)

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
        "webasyst_products_in_type_before": len(wa_products),
        "webasyst_matched_before_creation": len(wa_index),
        "webasyst_name_fallback_matches": wa_fallback,
        "webasyst_ambiguous": dict(list(wa_ambiguous.items())[:100]),
        "webasyst_created": 0,
        "webasyst_would_create": 0,
        "webasyst_existing_rerouted_to_type": 0,
    })

    missing_wa_keys = [
        key for key in sorted(feed_items)
        if key not in wa_index and key not in wa_ambiguous
    ]

    for key in missing_wa_keys:
        item = feed_items[key]
        code = item["vendor_code"]
        try:
            exact = global_wa_exact_sku(wa, code)
            # De-duplicate same product/SKU pair if API search returns repeated rows.
            uniq = {}
            for product, sku in exact:
                uniq[(s(product.get("id")), s(sku.get("id")))] = (product, sku)
            exact = list(uniq.values())

            if len(exact) > 1:
                report["errors"].append({
                    "stage": "webasyst_duplicate_guard",
                    "vendor_code": code,
                    "message": f"Найдено {len(exact)} карточек Webasyst с точным SKU",
                })
                continue

            if len(exact) == 1:
                product, sku = exact[0]
                pid = s(product.get("id"))
                if not args.dry_run and s(product.get("type_id")) != type_id:
                    wa.call(
                        "shop.product.update",
                        http_method="POST",
                        params={"id": pid},
                        data={"type_id": type_id},
                    )
                    product["type_id"] = type_id
                    report["webasyst_existing_rerouted_to_type"] += 1
                wa_index[key] = (product, sku)
                continue

            if args.dry_run:
                report["webasyst_would_create"] += 1
                continue

            created = wa.call(
                "shop.product.add",
                http_method="POST",
                data={
                    "name": item["name"] or code,
                    "url": product_url(item["name"], code),
                    "type_id": type_id,
                    "currency": "RUB",
                    "summary": extimg_summary(item.get("pictures") or []),
                    "description": item.get("description") or "",
                    "status": 1,
                    "skus": [{
                        "price": money_str(item["customer_price"]),
                        "compare_price": money_str(item["compare_price"]),
                        "stock": {stock_id: str(item["stock"])},
                        "available": 1 if item["stock"] > 0 else 0,
                        "status": 1,
                    }],
                },
            )
            product_id = extract_product_id(created)
            if not product_id:
                raise RuntimeError(
                    f"shop.product.add did not return product id: {str(created)[:300]}"
                )

            skus = get_product_skus(wa, product_id)
            if len(skus) != 1:
                raise RuntimeError(
                    f"New Webasyst product {product_id} unexpectedly has {len(skus)} SKUs"
                )
            sku_row = skus[0]
            wa.call(
                "shop.product.skus.update",
                http_method="POST",
                params={"id": s(sku_row.get("id"))},
                data={
                    "sku": code,
                    "price": money_str(item["customer_price"]),
                    "compare_price": money_str(item["compare_price"]),
                    "stock": {stock_id: str(item["stock"])},
                    "available": 1 if item["stock"] > 0 else 0,
                    "status": 1,
                },
            )
            sku_row["sku"] = code
            wa_index[key] = ({"id": product_id, "type_id": type_id}, sku_row)
            report["webasyst_created"] += 1
        except Exception as exc:
            report["errors"].append({
                "stage": "webasyst_create",
                "vendor_code": code,
                "message": str(exc)[:1000],
            })

    report["webasyst_matched_after_creation"] = len(wa_index)
    report["webasyst_unmatched_after_creation"] = [
        feed_items[k]["vendor_code"]
        for k in sorted(set(feed_items) - set(wa_index))
        if k not in wa_ambiguous
    ][:100]

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
    expected_existing = min(len(feed_items), max(kit_brand_count, len(wa_products)))
    coverage_floor = max(1, expected_existing - 3)
    coverage_ok = (
        len(kit_index) >= coverage_floor
        and len(wa_index) >= coverage_floor
    )
    if not coverage_ok:
        report["warnings"].append({
            "stage": "coverage",
            "message": (
                f"Неполное сопоставление: KIT {len(kit_index)}/{kit_brand_count}, "
                f"Webasyst {len(wa_index)}/{len(wa_products)}"
            ),
        })
    report["status"] = "ok" if not report["errors"] and coverage_ok else "degraded"
    report["complete"] = not report["errors"] and coverage_ok

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
