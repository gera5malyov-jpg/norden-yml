#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import threading
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urljoin

import requests

ROOT = Path(__file__).resolve().parent
REPORT_PATH = ROOT / "webasyst_n100_to_kit_import_report.json"
KIT_BASE = "https://api.kit.yandex.net"
TYPE_NAME = "NORDEN-100"
BRAND = "Norden"
EXCLUDED_FEATURES = {
    "ндс", "поставщик", "остаток", "остатки", "склад", "наличие",
}
ARTICLE_TITLE = "Артикул"
CODE_SITE_TITLE = "Код для сайта"


def s(v):
    return str(v or "").strip()


def nt(v):
    return re.sub(r"[^0-9a-zа-яё]+", "", unicodedata.normalize("NFKC", s(v)).casefold())


def article_key(v):
    return unicodedata.normalize("NFKC", s(v)).casefold()


def dec(v):
    try:
        return Decimal(str(v).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class WebasystAPIError(RuntimeError):
    pass


class WebasystClient:
    def __init__(self):
        self.base_url = (os.getenv("WEBASYST_BASE_URL") or "https://profikompany.ru").rstrip("/")
        self.token = (os.getenv("WEBASYST_API_TOKEN") or "").strip()
        if not self.token:
            raise WebasystAPIError("WEBASYST_API_TOKEN is not set")
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": "webasyst-n100-kit-import/1.0"})
        self._last = 0.0
        self.min_interval = 0.18

    def call(self, method, params=None):
        p = dict(params or {})
        p["format"] = "json"
        p["access_token"] = self.token
        url = f"{self.base_url}/api.php/{method}"
        for attempt in range(10):
            delay = self.min_interval - (time.monotonic() - self._last)
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()
            r = self.session.get(url, params=p, timeout=90)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After") or min(30, 2 * (attempt + 1))))
                continue
            if r.status_code >= 500:
                time.sleep(min(20, 2 ** attempt))
                continue
            try:
                data = r.json()
            except Exception:
                raise WebasystAPIError(f"non-JSON HTTP {r.status_code}")
            if r.status_code >= 400:
                raise WebasystAPIError(f"HTTP {r.status_code}: {str(data)[:800]}")
            if isinstance(data, dict) and data.get("error"):
                raise WebasystAPIError(f"{data.get('error')}: {data.get('error_description') or ''}")
            return data
        raise WebasystAPIError(f"retries exhausted: {method}")


class KitClient:
    """Dedicated KIT client for this explicit Webasyst->KIT import only.

    Intentionally independent from sync_norden_kit.KitClient so the global
    STOP_ALL_NORDEN guard remains active for every normal Norden workflow.
    """
    def __init__(self):
        token = (os.getenv("YANDEX_KIT_TOKEN") or "").strip()
        if not token:
            raise RuntimeError("YANDEX_KIT_TOKEN is not set")
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self.session = requests.Session()
        self._lock = threading.Lock()
        self._last = 0.0

    def request(self, method, path, *, params=None, body=None, merge_patch=False, timeout=120):
        url = KIT_BASE + path
        for attempt in range(12):
            headers = dict(self.headers)
            if body is not None:
                headers["Content-Type"] = "application/merge-patch+json" if merge_patch else "application/json"
            r = self.session.request(method, url, params=params, json=body, headers=headers, timeout=timeout)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After") or min(30, 1.5 * (attempt + 1))))
                continue
            if r.status_code >= 500:
                time.sleep(min(20, 2 ** attempt))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"KIT HTTP {r.status_code} {path}: {r.text[:800]}")
            if not r.content:
                return {}
            return r.json()
        raise RuntimeError(f"KIT retries exhausted: {method} {path}")

    @staticmethod
    def items(payload):
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        for k in ("items", "variants", "categories", "characteristics", "warehouses", "products", "results"):
            if isinstance(payload.get(k), list):
                return payload[k]
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
        return []

    @staticmethod
    def total(payload):
        if not isinstance(payload, dict):
            return None
        for k in ("total", "total_count"):
            if isinstance(payload.get(k), int):
                return payload[k]
        meta = payload.get("meta")
        if isinstance(meta, dict):
            for k in ("total", "total_count"):
                if isinstance(meta.get(k), int):
                    return meta[k]
        return None

    def iter_collection(self, path, params=None):
        page = 1
        while True:
            q = dict(params or {})
            q.update({"page": page, "per_page": 100})
            payload = self.request("GET", path, params=q)
            rows = self.items(payload)
            for row in rows:
                if isinstance(row, dict):
                    yield row
            total = self.total(payload)
            if not rows or (total is not None and page * 100 >= total) or (total is None and len(rows) < 100):
                break
            page += 1

    def characteristics(self):
        return list(self.iter_collection("/v1/characteristics", {"status": ["ACTIVE"]}))

    def categories(self):
        return list(self.iter_collection("/v1/categories", {"status": ["ACTIVE"]}))

    def warehouses(self):
        return list(self.iter_collection("/v1/warehouses", {"status": "ACTIVE"}))

    def create_characteristic(self, title):
        return self.request("POST", "/v1/characteristics", body={"title": title, "type": "STRING", "select_mode": "SINGLE"})

    def create_category(self, title, parent_id=None):
        body = {"title": title}
        if parent_id:
            body["parent_id"] = parent_id
        return self.request("POST", "/v1/categories", body=body)

    def create_product(self, category_id):
        return self.request("POST", "/v1/products", body={"category_ids": [category_id]})

    def create_variant(self, body):
        return self.request("POST", "/v1/variants", body=body)

    def patch_variant(self, variant_id, body):
        return self.request("PATCH", f"/v1/variants/{variant_id}", body=body, merge_patch=True)

    def upload_image_url(self, url):
        # KIT file upload requires multipart; keep isolated from JSON request helper.
        for attempt in range(8):
            rr = requests.get(url, timeout=90, headers={"User-Agent": "Mozilla/5.0"})
            rr.raise_for_status()
            name = url.split("?")[0].rstrip("/").split("/")[-1] or "image.jpg"
            mime = rr.headers.get("Content-Type") or "image/jpeg"
            r = self.session.post(
                KIT_BASE + "/v1/files",
                files={"file": (name, rr.content, mime)},
                headers=self.headers,
                timeout=180,
            )
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After") or 3 + attempt))
                continue
            if r.status_code >= 500:
                time.sleep(min(15, 2 ** attempt))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"KIT image HTTP {r.status_code}: {r.text[:500]}")
            return r.json() if r.content else {}
        raise RuntimeError("KIT image upload retries exhausted")

    def scan_all_variants_parallel(self, workers=10):
        first = self.request("GET", "/v1/variants", params={"page": 1, "per_page": 100})
        rows = self.items(first)
        total = self.total(first)
        for row in rows:
            if isinstance(row, dict):
                yield row
        if not total or total <= len(rows):
            return
        pages = (total + 99) // 100

        def get_page(page):
            # Separate session per worker avoids shared Session contention.
            headers = dict(self.headers)
            for attempt in range(12):
                r = requests.get(
                    KIT_BASE + "/v1/variants",
                    headers=headers,
                    params={"page": page, "per_page": 100},
                    timeout=120,
                )
                if r.status_code == 429:
                    time.sleep(float(r.headers.get("Retry-After") or min(20, 1 + attempt)))
                    continue
                if r.status_code >= 500:
                    time.sleep(min(15, 2 ** attempt))
                    continue
                if r.status_code >= 400:
                    raise RuntimeError(f"KIT scan page {page}: HTTP {r.status_code}")
                data = r.json()
                return page, self.items(data)
            raise RuntimeError(f"KIT scan page {page}: retries exhausted")

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(get_page, p) for p in range(2, pages + 1)]
            done = 1
            for fut in concurrent.futures.as_completed(futures):
                page, batch = fut.result()
                done += 1
                if done % 100 == 0 or done == pages:
                    print(f"KIT article scan: {done}/{pages} pages", flush=True)
                for row in batch:
                    if isinstance(row, dict):
                        yield row


def listify(payload, keys=()):
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys:
        v = payload.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
        if isinstance(v, dict):
            return [x for x in v.values() if isinstance(x, dict)]
    if payload and all(isinstance(v, dict) for v in payload.values()):
        return list(payload.values())
    return []


def product_skus(product):
    rows = product.get("skus")
    if isinstance(rows, dict):
        return [x for x in rows.values() if isinstance(x, dict)]
    if isinstance(rows, list):
        return [x for x in rows if isinstance(x, dict)]
    return []


def feature_value(v):
    if v in (None, ""):
        return ""
    if isinstance(v, dict):
        for k in ("value", "name", "title"):
            if s(v.get(k)):
                return s(v.get(k))
        vals = [feature_value(x) for x in v.values()]
        return " | ".join(dict.fromkeys(x for x in vals if x))
    if isinstance(v, (list, tuple)):
        vals = [feature_value(x) for x in v]
        return " | ".join(dict.fromkeys(x for x in vals if x))
    return s(v)


def current_char_value(row, char_id):
    for c in row.get("characteristics") or []:
        if s(c.get("characteristic_id")) == char_id:
            vals = c.get("values") or []
            return s(c.get("value") or (vals[0] if vals else ""))
    return ""


def load_wa_products(wa, type_id):
    out = []
    offset = 0
    while True:
        payload = wa.call("shop.product.search", {
            "hash": f"type/{type_id}",
            "offset": offset,
            "limit": 1000,
            "fields": "*,skus,stock_counts",
        })
        batch = listify(payload, ("products", "items"))
        total = (payload.get("count") or payload.get("total_count")) if isinstance(payload, dict) else None
        out.extend(batch)
        if not batch or len(batch) < 1000:
            break
        if total not in (None, "") and len(out) >= int(total):
            break
        offset += len(batch)
    return out


def flatten_category_tree(payload):
    by_id = {}
    def walk(node, parents):
        if not isinstance(node, dict):
            return
        cid = s(node.get("id"))
        title = s(node.get("name") or node.get("title"))
        here = parents + ([title] if title else [])
        if cid:
            by_id[cid] = here
        children = node.get("children") or node.get("childs") or node.get("categories") or []
        if isinstance(children, dict):
            children = list(children.values())
        if isinstance(children, list):
            for child in children:
                walk(child, here)
    rows = listify(payload, ("categories", "items"))
    for row in rows:
        walk(row, [])
    return by_id


def extract_category_ids(info, basic):
    vals = []
    for obj in (info, basic):
        if not isinstance(obj, dict):
            continue
        for k in ("category_id", "category"):
            v = obj.get(k)
            if isinstance(v, (str, int)) and s(v):
                vals.append(s(v))
        for k in ("category_ids", "categories"):
            v = obj.get(k)
            if isinstance(v, dict):
                for key, item in v.items():
                    if s(key).isdigit() or s(key):
                        vals.append(s(key))
                    if isinstance(item, dict) and s(item.get("id")):
                        vals.append(s(item.get("id")))
                    elif isinstance(item, (str, int)) and s(item):
                        vals.append(s(item))
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict) and s(item.get("id")):
                        vals.append(s(item.get("id")))
                    elif isinstance(item, (str, int)) and s(item):
                        vals.append(s(item))
    return list(dict.fromkeys(vals))


def stock_total(sku_row, product):
    raw = sku_row.get("stock_counts")
    if raw in (None, "", {}, []):
        raw = product.get("stock_counts")
    vals = []
    if isinstance(raw, dict):
        raw = list(raw.values())
    if isinstance(raw, list):
        for v in raw:
            if isinstance(v, dict):
                v = v.get("count") or v.get("stock") or v.get("quantity")
            d = dec(v)
            if d is not None:
                vals.append(d)
    else:
        d = dec(raw)
        if d is not None:
            vals.append(d)
    if not vals:
        d = dec(sku_row.get("count") or product.get("count"))
        if d is not None:
            vals.append(d)
    return max(0, int(sum(vals))) if vals else 0


def image_urls(info, base_url):
    rows = info.get("images") or [] if isinstance(info, dict) else []
    if isinstance(rows, dict):
        rows = list(rows.values())
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        url = s(row.get("url_big") or row.get("url") or row.get("url_thumb"))
        if url:
            out.append(urljoin(base_url, url))
    return list(dict.fromkeys(out))


def main():
    wa = WebasystClient()
    kit = KitClient()
    base_url = wa.base_url.rstrip("/") + "/"

    report = {
        "started_at": now_iso(),
        "mode": "one-time Webasyst NORDEN-100 -> KIT, article-only create gate",
        "supplier_api_used": False,
        "normal_norden_workflows_enabled": False,
        "existing_cards_modified": 0,
        "webasyst_products": 0,
        "webasyst_skus": 0,
        "kit_variants_scanned": 0,
        "kit_existing_articles": 0,
        "skipped_existing_article": 0,
        "skipped_duplicate_article_in_webasyst": 0,
        "skipped_sku_conflict_without_article": 0,
        "missing_category_skipped": 0,
        "created": 0,
        "categories_created": 0,
        "characteristics_created": 0,
        "images_uploaded": 0,
        "image_errors": 0,
        "errors": [],
        "created_items": [],
        "skipped_items": [],
        "complete": False,
    }

    # Exact Webasyst type.
    types = listify(wa.call("shop.type.getList"))
    matches = [x for x in types if nt(x.get("name") or x.get("title")) == nt(TYPE_NAME)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one Webasyst type {TYPE_NAME}, found {len(matches)}")
    type_id = s(matches[0].get("id"))
    report["type_id"] = type_id

    products = load_wa_products(wa, type_id)
    report["webasyst_products"] = len(products)

    # Webasyst categories and feature names.
    category_paths = {}
    try:
        category_paths = flatten_category_tree(wa.call("shop.category.getTree"))
    except Exception as exc:
        report["errors"].append({"stage": "category_tree", "error": str(exc)[:1000]})

    feature_defs = listify(wa.call("shop.feature.getList"), ("features", "items"))
    feature_title_by_code = {
        s(x.get("code")): s(x.get("name") or x.get("title"))
        for x in feature_defs if s(x.get("code"))
    }

    # KIT characteristics.
    chars = kit.characteristics()
    char_by_title = defaultdict(list)
    for row in chars:
        if s(row.get("id")) and s(row.get("title")):
            char_by_title[nt(row.get("title"))].append(s(row.get("id")))
    article_ids = char_by_title.get(nt(ARTICLE_TITLE), [])
    if not article_ids:
        created = kit.create_characteristic(ARTICLE_TITLE)
        article_id = s(created.get("id"))
        if not article_id:
            raise RuntimeError("Could not create KIT characteristic Артикул")
        article_ids = [article_id]
        chars.append(created)
        char_by_title[nt(ARTICLE_TITLE)].append(article_id)
        report["characteristics_created"] += 1
    article_id = article_ids[0]

    # Build exact Article index across ALL KIT statuses and brands.
    existing_articles = defaultdict(list)
    existing_skus = defaultdict(list)
    seen_variant_ids = set()
    for row in kit.scan_all_variants_parallel(workers=10):
        vid = s(row.get("id"))
        if not vid or vid in seen_variant_ids:
            continue
        seen_variant_ids.add(vid)
        report["kit_variants_scanned"] += 1
        sku = s(row.get("sku"))
        if sku:
            existing_skus[article_key(sku)].append({
                "variant_id": vid, "sku": sku, "kit_id": row.get("kit_id"), "status": s(row.get("status"))
            })
        for aid in article_ids:
            av = current_char_value(row, aid)
            if av:
                existing_articles[article_key(av)].append({
                    "variant_id": vid, "article": av, "sku": sku,
                    "kit_id": row.get("kit_id"), "status": s(row.get("status")), "brand": s(row.get("brand"))
                })
    report["kit_existing_articles"] = len(existing_articles)
    print(f"KIT scan complete: variants={report['kit_variants_scanned']} unique_articles={report['kit_existing_articles']}", flush=True)

    # KIT categories and warehouses.
    kit_categories = kit.categories()
    category_index = defaultdict(list)
    for row in kit_categories:
        category_index[(s(row.get("parent_id")), nt(row.get("title")))].append(row)

    def ensure_category_path(path):
        parts = [s(x) for x in path if s(x)]
        while parts and nt(parts[0]) in {nt("Norden"), nt(TYPE_NAME)}:
            parts.pop(0)
        if not parts:
            return None
        parent_id = ""
        for title in parts:
            key = (parent_id, nt(title))
            rows = category_index.get(key, [])
            if rows:
                row = rows[0]
            else:
                row = kit.create_category(title, parent_id or None)
                if not s(row.get("id")):
                    raise RuntimeError(f"KIT category create returned no id for {title}")
                category_index[key].append(row)
                report["categories_created"] += 1
            parent_id = s(row.get("id"))
        return parent_id

    warehouses = kit.warehouses()
    wh_by_title = {nt(x.get("title") or x.get("name")): s(x.get("id")) for x in warehouses}
    msk_id = wh_by_title.get(nt("МСК"))
    if not msk_id:
        raise RuntimeError("KIT warehouse МСК not found")

    def get_or_create_char(title):
        key = nt(title)
        rows = char_by_title.get(key, [])
        if rows:
            return rows[0]
        created = kit.create_characteristic(title)
        cid = s(created.get("id"))
        if not cid:
            raise RuntimeError(f"Could not create characteristic {title}")
        char_by_title[key].append(cid)
        report["characteristics_created"] += 1
        return cid

    seen_wa_articles = set()

    for product in products:
        product_id = s(product.get("id"))
        for sku_row in product_skus(product):
            article = s(sku_row.get("sku"))
            if not article:
                continue
            report["webasyst_skus"] += 1
            key = article_key(article)

            # User's primary rule: exact Article already exists in KIT => do nothing at all.
            if key in existing_articles:
                report["skipped_existing_article"] += 1
                report["skipped_items"].append({
                    "article": article,
                    "reason": "Артикул уже существует в KIT — карточка не изменялась",
                    "matches": existing_articles[key][:10],
                })
                continue

            # Prevent duplicate creation if Webasyst itself repeats the same article.
            if key in seen_wa_articles:
                report["skipped_duplicate_article_in_webasyst"] += 1
                report["skipped_items"].append({"article": article, "reason": "Повтор Артикула внутри NORDEN-100 Webasyst"})
                continue
            seen_wa_articles.add(key)

            # Technical safety only: if SKU is already occupied but Article characteristic is absent/different,
            # do not overwrite that existing card and do not create a conflicting duplicate.
            if key in existing_skus:
                report["skipped_sku_conflict_without_article"] += 1
                report["skipped_items"].append({
                    "article": article,
                    "reason": "SKU уже занят в KIT, но точного Артикула нет — создание остановлено, существующая карточка не менялась",
                    "matches": existing_skus[key][:10],
                })
                continue

            try:
                info = wa.call("shop.product.getInfo", {"id": product_id})
                name = s((info or {}).get("name")) or s(product.get("name")) or article

                # Category from Webasyst source. Never invent a supplier category.
                cids = extract_category_ids(info, product)
                path = None
                for cid in cids:
                    if cid in category_paths and category_paths[cid]:
                        path = category_paths[cid]
                        break
                if not path:
                    report["missing_category_skipped"] += 1
                    report["skipped_items"].append({
                        "article": article, "name": name,
                        "reason": "В Webasyst не удалось определить категорию — значение не придумано",
                    })
                    continue
                category_id = ensure_category_path(path)
                if not category_id:
                    report["missing_category_skipped"] += 1
                    continue

                created_product = kit.create_product(category_id)
                kit_product_id = s(created_product.get("id"))
                if not kit_product_id:
                    raise RuntimeError("KIT did not return product id")

                sale = dec(sku_row.get("price"))
                compare = dec(sku_row.get("compare_price"))
                pricing = {}
                if sale is not None and sale > 0:
                    if compare is not None and compare > sale:
                        pricing["price"] = str(compare)
                        pricing["manual_discount_price"] = str(sale)
                    else:
                        pricing["price"] = str(sale)

                qty = stock_total(sku_row, product)
                status_raw = s((info or {}).get("status") or product.get("status")).casefold()
                status = "PUBLISHED" if status_raw not in {"0", "false", "off", "disabled"} else "HIDDEN"

                body = {
                    "sku": article,
                    "name": name,
                    "status": status,
                    "product_id": kit_product_id,
                    "brand": BRAND,
                    "stocks": [{"warehouse_id": msk_id, "quantity": qty, "reserved": 0}],
                }
                if pricing:
                    body["pricing"] = pricing

                created_variant = kit.create_variant(body)
                variant_id = s(created_variant.get("id"))
                if not variant_id:
                    raise RuntimeError("KIT did not return variant id")

                # Full Webasyst content, excluding private/service fields.
                raw_features = (info or {}).get("features") or {}
                characteristics = [{
                    "characteristic_id": article_id,
                    "value": article,
                    "values": [article],
                }]
                if isinstance(raw_features, dict):
                    for code, raw_value in raw_features.items():
                        title = feature_title_by_code.get(s(code), s(code))
                        value = feature_value(raw_value)
                        if not title or not value:
                            continue
                        if nt(title) == nt(ARTICLE_TITLE):
                            continue
                        if nt(title) in {nt(x) for x in EXCLUDED_FEATURES}:
                            continue
                        cid = get_or_create_char(title)
                        characteristics.append({
                            "characteristic_id": cid,
                            "value": value,
                            "values": [value],
                        })

                dedup = {x["characteristic_id"]: x for x in characteristics}
                patch = {"characteristics": list(dedup.values())}
                description = s((info or {}).get("description"))
                if description:
                    patch["description"] = description

                media = []
                for url in image_urls(info or {}, base_url):
                    try:
                        uploaded = kit.upload_image_url(url)
                        fid = s(uploaded.get("id"))
                        if fid:
                            media.append({"type": "IMAGE", "display_sequence": len(media), "image_id": fid})
                            report["images_uploaded"] += 1
                    except Exception as exc:
                        report["image_errors"] += 1
                        if len(report["errors"]) < 300:
                            report["errors"].append({"article": article, "stage": "image", "error": str(exc)[:600]})
                if media:
                    patch["media"] = media

                kit.patch_variant(variant_id, patch)

                full = kit.request("GET", f"/v1/variants/{variant_id}")
                kit_id = full.get("kit_id") if isinstance(full, dict) else None

                report["created"] += 1
                report["created_items"].append({
                    "article": article,
                    "webasyst_product_id": product_id,
                    "kit_variant_id": variant_id,
                    "kit_id": kit_id,
                    "name": name,
                    "category_path": path,
                    "msk_stock": qty,
                })
                existing_articles[key].append({
                    "variant_id": variant_id, "article": article, "sku": article,
                    "kit_id": kit_id, "status": status, "brand": BRAND,
                })
                existing_skus[key].append({"variant_id": variant_id, "sku": article, "kit_id": kit_id, "status": status})

                if report["created"] % 25 == 0:
                    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(f"Created {report['created']} KIT cards from Webasyst NORDEN-100", flush=True)

            except Exception as exc:
                report["errors"].append({
                    "article": article,
                    "webasyst_product_id": product_id,
                    "stage": "create",
                    "error": str(exc)[:1200],
                })

    report["complete"] = True
    report["finished_at"] = now_iso()
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "webasyst_products": report["webasyst_products"],
        "webasyst_skus": report["webasyst_skus"],
        "kit_variants_scanned": report["kit_variants_scanned"],
        "kit_existing_articles": report["kit_existing_articles"],
        "skipped_existing_article": report["skipped_existing_article"],
        "skipped_sku_conflict_without_article": report["skipped_sku_conflict_without_article"],
        "missing_category_skipped": report["missing_category_skipped"],
        "created": report["created"],
        "images_uploaded": report["images_uploaded"],
        "errors": len(report["errors"]),
        "existing_cards_modified": report["existing_cards_modified"],
        "supplier_api_used": report["supplier_api_used"],
        "complete": report["complete"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
