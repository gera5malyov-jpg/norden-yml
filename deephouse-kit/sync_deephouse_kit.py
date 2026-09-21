#!/usr/bin/env python3
import argparse
import json
import mimetypes
import os
import time
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlparse

import requests

FEED_URL = "http://deephouse.pro/upload/cutcat.xml"
KIT_API = "https://api.kit.yandex.net"
SKU_PREFIX = "DEEP-"
WEBASYST_VALUE = "336"
BRAND = "DEEPHOUSE"
ROOT_CATEGORY = "DEEPHOUSE"
WAREHOUSE_NAMES = ("МСК", "СПБ привозной")
REPORT_PATH = "deephouse-kit/last_sync_report.json"


def s(value):
    return str(value or "").strip()


def norm(value):
    return " ".join(s(value).casefold().split())


def local_name(tag):
    return tag.split("}")[-1]


def as_decimal(value):
    try:
        return Decimal(s(value).replace(",", ".").replace(" ", ""))
    except (InvalidOperation, ValueError):
        return None


def first_text(node, *tags, default=""):
    wanted = {x.casefold() for x in tags}
    for child in list(node):
        if local_name(child.tag).casefold() in wanted:
            value = s(child.text)
            if value:
                return value
    return default


def all_text(node, *tags):
    wanted = {x.casefold() for x in tags}
    out = []
    for child in list(node):
        if local_name(child.tag).casefold() in wanted:
            value = s(child.text)
            if value and value not in out:
                out.append(value)
    return out


def parse_bool(value):
    v = norm(value)
    if v in {"1", "true", "yes", "y", "да", "available", "in_stock", "instock"}:
        return True
    if v in {"0", "false", "no", "n", "нет", "unavailable", "out_of_stock", "outofstock"}:
        return False
    return None


def make_sku(article):
    article = s(article)
    if not article:
        return ""
    if article.upper().startswith(SKU_PREFIX):
        return article
    return SKU_PREFIX + article


def parse_stock(node):
    exact_fields = ("stock", "count", "quantity", "amount", "qty", "available_quantity", "stock_quantity")
    for field in exact_fields:
        value = first_text(node, field)
        if value:
            d = as_decimal(value)
            if d is not None:
                return max(0, int(d)), field, True

    for key in ("stock", "count", "quantity", "amount", "qty"):
        value = s(node.attrib.get(key))
        if value:
            d = as_decimal(value)
            if d is not None:
                return max(0, int(d)), "@" + key, True

    available_attr = s(node.attrib.get("available"))
    if available_attr:
        b = parse_bool(available_attr)
        if b is not None:
            return (1 if b else 0), "@available", False

    available_text = first_text(node, "available", "availability")
    if available_text:
        d = as_decimal(available_text)
        if d is not None:
            return max(0, int(d)), "available", True
        b = parse_bool(available_text)
        if b is not None:
            return (1 if b else 0), "available", False

    return None, "", False


def fetch_feed():
    last_error = None
    urls = [FEED_URL]
    if FEED_URL.startswith("http://"):
        urls.append("https://" + FEED_URL[len("http://"):])

    for url in urls:
        try:
            response = requests.get(
                url,
                timeout=180,
                headers={"User-Agent": "Mozilla/5.0 DEEPHOUSE-KIT-Sync"},
            )
            response.raise_for_status()
            return url, response.content
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Cannot download DEEPHOUSE feed: {last_error}")


def parse_feed():
    effective_url, content = fetch_feed()
    root = ET.fromstring(content)

    categories = {}
    for node in root.iter():
        if local_name(node.tag).casefold() != "category":
            continue
        cid = s(node.attrib.get("id"))
        title = s(node.text)
        if cid and title:
            categories[cid] = title

    offers = []
    for node in root.iter():
        if local_name(node.tag).casefold() not in {"offer", "product", "item"}:
            continue

        supplier_article = (
            first_text(node, "vendorCode", "vendor_code", "article", "sku", "code")
            or s(node.attrib.get("id"))
        )
        if not supplier_article:
            continue

        price = first_text(node, "price", "retail_price", "sale_price")
        stock, stock_source, stock_is_exact = parse_stock(node)

        params = {}
        for child in list(node):
            if local_name(child.tag).casefold() not in {"param", "property", "attribute"}:
                continue
            title = s(child.attrib.get("name")) or s(child.attrib.get("title"))
            value = s(child.text)
            if title and value:
                params.setdefault(title, [])
                if value not in params[title]:
                    params[title].append(value)

        offers.append({
            "source_id": s(node.attrib.get("id")),
            "supplier_article": supplier_article,
            "sku": make_sku(supplier_article),
            "name": first_text(node, "name", "title", default=supplier_article),
            "description": first_text(node, "description", "full_description", "text"),
            "price": price,
            "category_id": first_text(node, "categoryId", "category_id", "category"),
            "vendor_from_feed": first_text(node, "vendor", "brand"),
            "source_url": first_text(node, "url", "link"),
            "pictures": all_text(node, "picture", "image", "photo"),
            "params": params,
            "stock": stock,
            "stock_source": stock_source,
            "stock_is_exact": stock_is_exact,
        })

    return effective_url, categories, offers


class KitClient:
    def __init__(self, token):
        self.session = requests.Session()
        self.token = token
        self.last_request_at = 0.0

    def request(self, method, path, *, params=None, body=None, files=None, timeout=90):
        url = path if path.startswith("http") else KIT_API + path
        for attempt in range(15):
            delay = 0.65 - (time.monotonic() - self.last_request_at)
            if delay > 0:
                time.sleep(delay)
            self.last_request_at = time.monotonic()

            headers = {
                "Authorization": "Bearer " + self.token,
                "Accept": "application/json",
            }
            if method == "PATCH":
                headers["Content-Type"] = "application/merge-patch+json"

            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=body,
                    files=files,
                    headers=headers,
                    timeout=timeout,
                )
            except requests.RequestException:
                if attempt == 14:
                    raise
                time.sleep(min(15, attempt + 1))
                continue

            if response.status_code == 429:
                time.sleep(float(response.headers.get("Retry-After") or min(15, 2 + attempt)))
                continue
            if response.status_code >= 500:
                if attempt == 14:
                    response.raise_for_status()
                time.sleep(min(15, attempt + 1))
                continue

            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()
        raise RuntimeError("KIT request retries exhausted")

    def list_all(self, path, params=None, preferred_key=None):
        rows = []
        page = 1
        while True:
            query = dict(params or {})
            query.update({"page": page, "per_page": 100})
            payload = self.request("GET", path, params=query)
            batch = []
            if preferred_key and isinstance(payload.get(preferred_key), list):
                batch = payload[preferred_key]
            else:
                for value in payload.values():
                    if isinstance(value, list):
                        batch = value
                        break
            rows.extend(x for x in batch if isinstance(x, dict))
            total = payload.get("total_count") or payload.get("total")
            if not batch or len(batch) < 100 or (total is not None and len(rows) >= int(total)):
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
        return self.request("POST", "/v1/variants", body=body)

    def get_variant(self, variant_id):
        return self.request("GET", f"/v1/variants/{variant_id}")

    def patch_variant(self, variant_id, body):
        return self.request("PATCH", f"/v1/variants/{variant_id}", body=body)

    def archive_variant(self, variant_id):
        return self.request("POST", f"/v1/variants/{variant_id}/archive")

    def update_prices(self, items):
        if items:
            return self.request("POST", "/v1/variants/prices/bulk_update", body={"items": items})

    def update_stocks(self, items):
        if items:
            return self.request("POST", "/v1/variants/stocks/bulk_update", body={"items": items})

    def upload_image_url(self, url):
        response = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0 DEEPHOUSE-KIT-Sync"})
        response.raise_for_status()
        content_type = response.headers.get("Content-Type") or mimetypes.guess_type(urlparse(url).path)[0] or "image/jpeg"
        ext = os.path.splitext(urlparse(url).path)[1] or ".jpg"
        return self.request(
            "POST",
            "/v1/files",
            files={"file": ("deephouse" + ext[:10], response.content, content_type)},
            timeout=150,
        )


def price_payload(item):
    sale = as_decimal(item["price"])
    if sale is None or sale <= 0:
        return None
    old = (sale * Decimal("1.50")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sale = sale.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"price": str(old), "manual_discount_price": str(sale)}


def build_report(effective_url, categories, offers):
    exact_stock = sum(1 for x in offers if x["stock"] is not None and x["stock_is_exact"])
    bool_stock = sum(1 for x in offers if x["stock"] is not None and not x["stock_is_exact"])
    missing_stock = sum(1 for x in offers if x["stock"] is None)
    missing_price = sum(1 for x in offers if as_decimal(x["price"]) is None)
    sample = []
    for item in offers[:10]:
        sample.append({
            "source_id": item["source_id"],
            "supplier_article": item["supplier_article"],
            "sku": item["sku"],
            "name": item["name"],
            "price": item["price"],
            "stock": item["stock"],
            "stock_source": item["stock_source"],
            "stock_is_exact": item["stock_is_exact"],
            "category_id": item["category_id"],
            "vendor_from_feed": item["vendor_from_feed"],
            "pictures_count": len(item["pictures"]),
        })
    return {
        "status": "parsed",
        "feed_url_requested": FEED_URL,
        "feed_url_effective": effective_url,
        "source_products": len(offers),
        "source_categories": len(categories),
        "stock_exact_products": exact_stock,
        "stock_boolean_products": bool_stock,
        "stock_missing_products": missing_stock,
        "price_missing_products": missing_price,
        "sku_rule": "DEEP-<supplier article>",
        "webasyst_characteristic": "336",
        "brand": BRAND,
        "warehouse_rule": "same source stock on МСК and СПБ привозной",
        "price_rule": "customer price = feed price; crossed price = customer price * 1.50",
        "sample": sample,
        "processed": 0,
        "created": 0,
        "updated_existing": 0,
        "duplicate_variants_archived": 0,
        "images_uploaded": 0,
        "image_errors": 0,
        "price_updates": 0,
        "stock_updates": 0,
        "errors": [],
        "warnings": [],
        "complete": False,
    }


def save_report(report):
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def run(dry_run=False, max_items=0):
    effective_url, source_categories, offers = parse_feed()
    report = build_report(effective_url, source_categories, offers)

    if not offers:
        report["status"] = "error"
        report["errors"].append({"message": "No products found in feed"})
        save_report(report)
        return 1

    if dry_run:
        report["status"] = "dry_run_ok"
        report["complete"] = True
        save_report(report)
        return 0

    exact_or_zero = all(item["stock"] is not None for item in offers)
    if not exact_or_zero:
        report["status"] = "blocked"
        report["errors"].append({
            "message": "Some products have no stock field; live import stopped to prevent incorrect stock values."
        })
        save_report(report)
        return 2

    token = s(os.environ.get("YANDEX_KIT_TOKEN"))
    if not token:
        raise RuntimeError("YANDEX_KIT_TOKEN is not configured")

    if max_items and max_items > 0:
        offers = offers[:max_items]

    kit = KitClient(token)

    warehouses = kit.list_all("/v1/warehouses", {"status": "ACTIVE"}, "warehouses")
    warehouse_ids = {s(row.get("title")): s(row.get("id")) for row in warehouses if s(row.get("id"))}
    missing = [name for name in WAREHOUSE_NAMES if name not in warehouse_ids]
    if missing:
        raise RuntimeError("KIT warehouses not found: " + ", ".join(missing))

    categories = kit.list_all("/v1/categories", {"status": "ACTIVE"}, "categories")

    def ensure_category(title, parent_id=""):
        matches = [
            row for row in categories
            if norm(row.get("title")) == norm(title)
            and s(row.get("parent_id")) == s(parent_id)
        ]
        if matches:
            return s(matches[0].get("id"))
        created = kit.create_category(title, parent_id or None)
        cid = s(created.get("id"))
        if not cid:
            raise RuntimeError(f"KIT did not return category id for {title!r}")
        normalized = dict(created)
        normalized.setdefault("parent_id", parent_id)
        categories.append(normalized)
        return cid

    root_category_id = ensure_category(ROOT_CATEGORY)
    source_category_to_kit = {}
    for source_id, title in source_categories.items():
        source_category_to_kit[source_id] = ensure_category(title or source_id, root_category_id)

    characteristics = kit.list_all("/v1/characteristics", {"status": "ACTIVE"}, "characteristics")
    characteristic_cache = {}

    def ensure_characteristic(title):
        key = norm(title)
        if key in characteristic_cache:
            return characteristic_cache[key]
        matches = [row for row in characteristics if norm(row.get("title")) == key]
        if matches:
            string_matches = [
                row for row in matches
                if s(row.get("type")).upper() in ("STRING", "MULTIPLE_STRING")
            ]
            chosen = (string_matches or matches)[0]
            cid = s(chosen.get("id"))
            characteristic_cache[key] = cid
            return cid
        created = kit.create_characteristic(title)
        cid = s(created.get("id"))
        if not cid:
            raise RuntimeError(f"KIT did not return characteristic id for {title!r}")
        characteristics.append(created)
        characteristic_cache[key] = cid
        return cid

    def build_characteristics(item):
        pairs = []

        def add(title, value):
            value = s(value)
            if not title or not value:
                return
            if any(norm(existing_title) == norm(title) for existing_title, _ in pairs):
                return
            pairs.append((title, value))

        add("Webasyst", WEBASYST_VALUE)
        add("Артикул", item["sku"])
        add("Код для сайта", item["sku"])
        add("Артикул поставщика", item["supplier_article"])
        for title, values in item["params"].items():
            add(title, " / ".join(values))

        result = []
        for title, value in pairs:
            try:
                cid = ensure_characteristic(title)
                result.append({
                    "characteristic_id": cid,
                    "value": value,
                    "values": [value],
                })
            except Exception as exc:
                if len(report["warnings"]) < 300:
                    report["warnings"].append(f"{item['sku']}: characteristic {title!r}: {exc}")
        return result

    def find_exact_variants(sku):
        payload = kit.request("GET", "/v1/variants", params={"name": sku, "page": 1, "per_page": 100})
        rows = payload.get("variants") or payload.get("items") or payload.get("results") or []
        if isinstance(rows, dict):
            rows = rows.get("items") or []
        return [
            row for row in rows
            if s(row.get("sku")) == sku and s(row.get("status")).upper() != "ARCHIVED"
        ]

    def prepare_media(item):
        media = []
        for url in item["pictures"]:
            try:
                uploaded = kit.upload_image_url(url)
                file_id = s(uploaded.get("id"))
                if file_id:
                    media.append({
                        "type": "IMAGE",
                        "display_sequence": len(media),
                        "image_id": file_id,
                    })
                    report["images_uploaded"] += 1
            except Exception as exc:
                report["image_errors"] += 1
                if len(report["warnings"]) < 300:
                    report["warnings"].append(f"{item['sku']}: image failed {url}: {exc}")
        return media

    price_batch = []
    stock_batch = []

    for item in offers:
        sku = item["sku"]
        try:
            exact = find_exact_variants(sku)
            if len(exact) > 1:
                exact = sorted(
                    exact,
                    key=lambda row: (
                        int(row.get("kit_id") or 10**18),
                        s(row.get("created_at")),
                        s(row.get("id")),
                    ),
                )
                keeper = exact[0]
                for duplicate in exact[1:]:
                    duplicate_id = s(duplicate.get("id"))
                    if duplicate_id:
                        kit.archive_variant(duplicate_id)
                        report["duplicate_variants_archived"] += 1
                exact = [keeper]

            characteristics_payload = build_characteristics(item)
            pricing = price_payload(item)
            quantity = max(0, int(item["stock"] or 0))
            stock_rows = [
                {
                    "warehouse_id": warehouse_ids[warehouse_name],
                    "quantity": quantity,
                    "reserved": 0,
                }
                for warehouse_name in WAREHOUSE_NAMES
            ]

            if exact:
                variant = kit.get_variant(s(exact[0].get("id")))
                variant_id = s(variant.get("id"))
                patch = {}

                if s(variant.get("brand")) != BRAND:
                    patch["brand"] = BRAND
                if not s(variant.get("description")) and item["description"]:
                    patch["description"] = item["description"]
                if not s(variant.get("name")) and item["name"]:
                    patch["name"] = item["name"]

                existing_characteristics = list(variant.get("characteristics") or [])
                by_id = {
                    s(row.get("characteristic_id")): row
                    for row in existing_characteristics
                    if s(row.get("characteristic_id"))
                }
                for row in characteristics_payload:
                    by_id[s(row.get("characteristic_id"))] = row
                merged_characteristics = list(by_id.values())
                if merged_characteristics != existing_characteristics:
                    patch["characteristics"] = merged_characteristics

                if not (variant.get("media") or []) and item["pictures"]:
                    media = prepare_media(item)
                    if media:
                        patch["media"] = media

                if patch:
                    kit.patch_variant(variant_id, patch)

                if pricing:
                    price_batch.append({
                        "variant_id": variant_id,
                        "price": pricing["price"],
                        "manual_discount_price": pricing["manual_discount_price"],
                    })
                    report["price_updates"] += 1

                for row in stock_rows:
                    stock_batch.append({
                        "variant_id": variant_id,
                        "warehouse_id": row["warehouse_id"],
                        "quantity": row["quantity"],
                    })
                    report["stock_updates"] += 1
                report["updated_existing"] += 1
            else:
                category_id = source_category_to_kit.get(item["category_id"]) or root_category_id
                product = kit.create_product(category_id)
                product_id = s(product.get("id"))
                if not product_id:
                    raise RuntimeError("KIT did not return product id")

                media = prepare_media(item)
                body = {
                    "sku": sku,
                    "name": item["name"],
                    "description": item["description"],
                    "status": "PUBLISHED",
                    "product_id": product_id,
                    "brand": BRAND,
                    "stocks": stock_rows,
                    "characteristics": characteristics_payload,
                }
                if pricing:
                    body["pricing"] = pricing
                if media:
                    body["media"] = media

                created = kit.create_variant(body)
                if not s(created.get("id")):
                    raise RuntimeError("KIT did not return variant id")
                report["created"] += 1

            report["processed"] += 1

            if len(price_batch) >= 100:
                kit.update_prices(price_batch)
                price_batch.clear()
            if len(stock_batch) >= 200:
                kit.update_stocks(stock_batch)
                stock_batch.clear()

        except Exception as exc:
            report["errors"].append({"sku": sku, "message": str(exc)[:1000]})

    if price_batch:
        kit.update_prices(price_batch)
    if stock_batch:
        kit.update_stocks(stock_batch)

    report["status"] = "ok" if not report["errors"] else "degraded"
    report["complete"] = report["processed"] == len(offers) and not report["errors"]
    save_report(report)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-items", type=int, default=0)
    args = parser.parse_args()
    raise SystemExit(run(dry_run=args.dry_run, max_items=args.max_items))
