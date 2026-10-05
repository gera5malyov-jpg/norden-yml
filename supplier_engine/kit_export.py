from __future__ import annotations

import os
import re
import time
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlparse

import requests

from .webasyst_sync import index_by_supplier_sku_name

KIT_BASE = "https://api.kit.yandex.net"


class KitExportError(RuntimeError):
    def __init__(self, message, result=None):
        super().__init__(message)
        self.result = result or {}


def _s(value):
    return str(value or "").strip()


def _norm(value):
    return re.sub(r"[^0-9a-zа-яё]+", "", _s(value).casefold())


def _money(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return None


def _qty(value):
    try:
        return max(0, int(float(value or 0)))
    except Exception:
        return 0


def _listify(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "products", "categories", "warehouses", "characteristics", "variants", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        if isinstance(payload.get("data"), dict):
            for key in ("items", "products", "categories"):
                value = payload["data"].get(key)
                if isinstance(value, list):
                    return value
    return []


class KitClient:
    def __init__(self, token=None):
        token = _s(token or os.getenv("YANDEX_KIT_TOKEN"))
        if not token:
            raise KitExportError("YANDEX_KIT_TOKEN is not configured")
        self.session = requests.Session()
        self.headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        self.last_request_at = 0.0
        self.min_request_interval = 0.42

    def _pace(self):
        delay = self.min_request_interval - (time.monotonic() - self.last_request_at)
        if delay > 0:
            time.sleep(delay)
        self.last_request_at = time.monotonic()

    def request(self, method, path, *, params=None, body=None, files=None, content_type=None, timeout=120):
        url = KIT_BASE + path
        for attempt in range(10):
            self._pace()
            headers = dict(self.headers)
            if content_type:
                headers["Content-Type"] = content_type
            elif body is not None and files is None:
                headers["Content-Type"] = "application/json"
            response = self.session.request(
                method,
                url,
                params=params,
                json=body if files is None else None,
                files=files,
                headers=headers,
                timeout=timeout,
            )
            if response.status_code == 429:
                time.sleep(float(response.headers.get("Retry-After") or min(30, 3 * (attempt + 1))))
                continue
            if response.status_code >= 500:
                time.sleep(min(20, 2 ** attempt))
                continue
            if response.status_code >= 400:
                raise KitExportError("KIT HTTP %s %s: %s" % (response.status_code, path, response.text[:800]))
            if not response.content:
                return {}
            try:
                return response.json()
            except Exception:
                return {"raw": response.text}
        raise KitExportError("KIT retries exhausted: %s %s" % (method, path))

    def collection(self, path, params=None):
        out = []
        page = 1
        while True:
            q = dict(params or {})
            q.update({"page": page, "per_page": 100})
            payload = self.request("GET", path, params=q)
            rows = _listify(payload)
            out.extend(row for row in rows if isinstance(row, dict))
            total = None
            if isinstance(payload, dict):
                total = payload.get("total_count", payload.get("total"))
            if not rows or (isinstance(total, int) and len(out) >= total) or (total is None and len(rows) < 100):
                return out
            page += 1

    def warehouses(self):
        return self.collection("/v1/warehouses", {"status": "ACTIVE"})

    def categories(self):
        return self.collection("/v1/categories", {"status": ["ACTIVE"]})

    def characteristics(self):
        return self.collection("/v1/characteristics", {"status": ["ACTIVE"]})

    def variants(self):
        return self.collection("/v1/variants")

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

    def create_product(self, category_ids):
        return self.request("POST", "/v1/products", body={"category_ids": list(category_ids)})

    def patch_product(self, product_id, category_ids):
        return self.request(
            "PATCH",
            "/v1/products/%s" % product_id,
            body={"category_ids": list(category_ids)},
            content_type="application/json",
        )

    def create_variant(self, body):
        return self.request("POST", "/v1/variants", body=body)

    def get_variant(self, variant_id):
        return self.request("GET", "/v1/variants/%s" % variant_id)

    def patch_variant(self, variant_id, body):
        return self.request(
            "PATCH",
            "/v1/variants/%s" % variant_id,
            body=body,
            content_type="application/merge-patch+json",
        )

    def upload_image_url(self, url):
        response = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        name = os.path.basename(urlparse(url).path) or "image.jpg"
        mime = response.headers.get("Content-Type") or "image/jpeg"
        return self.request("POST", "/v1/files", files={"file": (name, response.content, mime)}, timeout=180)

    def public_image_urls(self, media):
        ordered = [
            row for row in (media or [])
            if isinstance(row, dict) and _s(row.get("type")).upper() == "IMAGE" and _s(row.get("image_id"))
        ]
        ordered.sort(key=lambda row: int(row.get("display_sequence") or 0))
        urls = []
        for row in ordered:
            meta = self.request("GET", "/v1/files/%s" % _s(row.get("image_id")))
            url = _s(meta.get("url")) if isinstance(meta, dict) else ""
            if url and url not in urls:
                urls.append(url)
        return urls


def _category_ids(product):
    raw = product.get("categories") if isinstance(product, dict) else None
    if isinstance(raw, dict):
        raw = list(raw.values()) if raw else []
    ids = []
    for row in raw or []:
        if isinstance(row, dict):
            cid = _s(row.get("id"))
        else:
            cid = _s(row)
        if cid and cid not in ids:
            ids.append(cid)
    return ids


def _webasyst_category_path(wa, category_id, cache):
    category_id = _s(category_id)
    if category_id in cache:
        return cache[category_id]
    parents = _listify(wa.call("shop.category.getParents", params={"id": category_id, "reverse": 1}))
    info = wa.call("shop.category.getInfo", params={"id": category_id})
    path = []
    for row in parents:
        name = _s(row.get("name") if isinstance(row, dict) else "")
        if name:
            path.append(name)
    if isinstance(info, dict):
        name = _s(info.get("name"))
        if name and (not path or _norm(path[-1]) != _norm(name)):
            path.append(name)
    if not path:
        raise KitExportError("Webasyst category %s has no readable path" % category_id)
    cache[category_id] = path
    return path


def _kit_category_index(categories):
    by_key = defaultdict(list)
    for row in categories:
        cid = _s(row.get("id"))
        title = _s(row.get("title") or row.get("name"))
        parent = _s(row.get("parent_id"))
        if cid and title:
            by_key[(parent, _norm(title))].append(row)
    return by_key


def _ensure_kit_category_path(kit, path, categories, stats):
    index = _kit_category_index(categories)
    parent = ""
    for title in path:
        matches = index.get((parent, _norm(title)), [])
        if len(matches) > 1:
            raise KitExportError("KIT category is ambiguous: %s" % " > ".join(path))
        if matches:
            row = matches[0]
        else:
            row = kit.create_category(title, parent or None)
            if not _s(row.get("id")):
                raise KitExportError("KIT did not return category id for %s" % title)
            categories.append(row)
            stats["categories_created"] += 1
            index = _kit_category_index(categories)
        parent = _s(row.get("id"))
    return parent


def _kit_characteristic_index(rows):
    by_title = defaultdict(list)
    for row in rows:
        title = _s(row.get("title") or row.get("name"))
        if title:
            by_title[_norm(title)].append(row)
    return by_title


def _ensure_characteristics(kit, names, rows, stats):
    out = {}
    index = _kit_characteristic_index(rows)
    for title in sorted({_s(x) for x in names if _s(x)}):
        matches = index.get(_norm(title), [])
        if len(matches) > 1:
            raise KitExportError("KIT characteristic is ambiguous: %s" % title)
        if matches:
            row = matches[0]
        else:
            row = kit.create_characteristic(title)
            if not _s(row.get("id")):
                raise KitExportError("KIT did not return characteristic id for %s" % title)
            rows.append(row)
            stats["characteristics_created"] += 1
            index = _kit_characteristic_index(rows)
        out[title] = _s(row.get("id"))
    return out


def _characteristic_payload(desired, characteristic_ids, existing=None):
    merged = {}
    for row in existing or []:
        if isinstance(row, dict) and _s(row.get("characteristic_id")):
            merged[_s(row["characteristic_id"])] = row
    for title, value in (desired.get("characteristics") or {}).items():
        cid = characteristic_ids.get(_s(title))
        if not cid or value in (None, ""):
            continue
        value = _s(value)
        merged[cid] = {"characteristic_id": cid, "value": value, "values": [value]}
    return list(merged.values())


def _extract_source_images(desired):
    return list(dict.fromkeys(_s(url) for url in (desired.get("images") or []) if _s(url)))[:20]


def _unique_image_aliases(products):
    aliases = {}
    ambiguous = set()
    for product in products:
        code = _s(product.supplier_sku)
        for url in product.images or []:
            url = _s(url)
            if not code or not url:
                continue
            previous = aliases.get(url)
            if previous and previous != code:
                ambiguous.add(url)
            else:
                aliases[url] = code
    for url in ambiguous:
        aliases.pop(url, None)
    return aliases


def _set_webasyst_kit_data(wa, product_id, kit_id, image_urls):
    data = {"features": {"kit_id": str(int(kit_id))}}
    if image_urls:
        data["summary"] = "[extimg]\n" + "\n".join(image_urls) + "\n[/extimg]"
    wa.call("shop.product.update", http_method="POST", params={"id": str(product_id)}, data=data)


def sync_supplier_to_kit(wa, products, config):
    rules = config.get("rules") or {}
    if not rules.get("export_to_kit", False):
        return {"status": "disabled"}

    token = _s(os.getenv("YANDEX_KIT_TOKEN"))
    if not token:
        raise KitExportError("YANDEX_KIT_TOKEN is not configured")

    web = config.get("webasyst") or {}
    type_id = web.get("type_id")
    if not type_id:
        raise KitExportError("Webasyst type is required for KIT export")

    products = list(products)
    source_by_supplier = {p.supplier_sku: p for p in products if _s(p.supplier_sku)}
    aliases = {p.sku: p.supplier_sku for p in products if _s(p.sku) and _s(p.supplier_sku)}
    by_supplier = index_by_supplier_sku_name(
        wa,
        type_id,
        list(source_by_supplier),
        aliases,
        _unique_image_aliases(products),
    )

    stats = {
        "status": "ok",
        "eligible": 0,
        "skipped_without_category": 0,
        "skipped_not_unique": 0,
        "created": 0,
        "updated": 0,
        "categories_created": 0,
        "characteristics_created": 0,
        "images_uploaded": 0,
        "webasyst_backfilled": 0,
        "errors": [],
    }

    eligible = []
    category_cache = {}
    all_feature_names = set()
    for supplier_sku, desired_obj in source_by_supplier.items():
        matches = by_supplier.get(supplier_sku) or []
        if len(matches) != 1:
            stats["skipped_not_unique"] += 1
            continue
        current_product, current_sku = matches[0]
        category_ids = _category_ids(current_product)
        if not category_ids:
            stats["skipped_without_category"] += 1
            continue
        paths = [_webasyst_category_path(wa, cid, category_cache) for cid in category_ids]
        desired = {
            "supplier_sku": desired_obj.supplier_sku,
            "name": desired_obj.name,
            "purchase_price": desired_obj.purchase_price,
            "stock": desired_obj.stock,
            "brand": desired_obj.brand,
            "images": list(desired_obj.images or []),
            "characteristics": dict(desired_obj.characteristics or {}),
        }
        all_feature_names.update(desired["characteristics"].keys())
        eligible.append({
            "supplier_sku": supplier_sku,
            "product_id": current_product.get("id"),
            "sku_id": current_sku.get("id"),
            "category_paths": paths,
            "desired": desired,
        })
    stats["eligible"] = len(eligible)

    kit = KitClient(token)
    warehouses = kit.warehouses()
    warehouse_by_name = {_norm(row.get("title") or row.get("name")): _s(row.get("id")) for row in warehouses}
    msk_id = warehouse_by_name.get(_norm("МСК"))
    spb_id = warehouse_by_name.get(_norm("СПБ привозной"))
    if not msk_id or not spb_id:
        raise KitExportError("KIT warehouses МСК / СПБ привозной not found", stats)

    categories = kit.categories()
    kit_characteristics = kit.characteristics()
    characteristic_ids = _ensure_characteristics(kit, all_feature_names, kit_characteristics, stats)

    variants_by_sku = defaultdict(list)
    for row in kit.variants():
        sku = _s(row.get("sku"))
        if sku:
            variants_by_sku[sku].append(row)

    for item in eligible:
        supplier_sku = item["supplier_sku"]
        try:
            leaf_ids = []
            for path in item["category_paths"]:
                leaf = _ensure_kit_category_path(kit, path, categories, stats)
                if leaf and leaf not in leaf_ids:
                    leaf_ids.append(leaf)
            if not leaf_ids:
                raise KitExportError("No KIT categories resolved")

            desired = item["desired"]
            purchase = _money(desired.get("purchase_price"))
            quantity = _qty(desired.get("stock"))
            pricing = None
            if purchase is not None and purchase > 0:
                pricing = {
                    "price": str((purchase * Decimal("1.60")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                    "manual_discount_price": str((purchase * Decimal("1.25")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                }

            matches = variants_by_sku.get(supplier_sku) or []
            if len(matches) > 1:
                raise KitExportError("Duplicate exact KIT SKU %s" % supplier_sku)

            source_images = _extract_source_images(desired)
            if matches:
                variant = kit.get_variant(_s(matches[0].get("id")))
                variant_id = _s(variant.get("id"))
                product_id = _s(variant.get("product_id"))
                if not variant_id or not product_id:
                    raise KitExportError("KIT variant %s has incomplete identifiers" % supplier_sku)
                kit.patch_product(product_id, leaf_ids)
                media = [row for row in (variant.get("media") or []) if isinstance(row, dict) and _s(row.get("type")).upper() == "IMAGE"]
                if source_images and len(media) != len(source_images):
                    media = []
                    for url in source_images:
                        uploaded = kit.upload_image_url(url)
                        file_id = _s(uploaded.get("id"))
                        if not file_id:
                            raise KitExportError("KIT image upload returned no id for %s" % url)
                        media.append({"type": "IMAGE", "display_sequence": len(media), "image_id": file_id})
                        stats["images_uploaded"] += 1
                patch = {
                    "sku": supplier_sku,
                    "name": desired.get("name") or supplier_sku,
                    "brand": desired.get("brand") or "",
                    "status": "PUBLISHED",
                    "stocks": [
                        {"warehouse_id": msk_id, "quantity": quantity, "reserved": 0},
                        {"warehouse_id": spb_id, "quantity": quantity, "reserved": 0},
                    ],
                    "characteristics": _characteristic_payload(desired, characteristic_ids, variant.get("characteristics") or []),
                }
                if pricing:
                    patch["pricing"] = pricing
                if media:
                    patch["media"] = media
                kit.patch_variant(variant_id, patch)
                variant = kit.get_variant(variant_id)
                stats["updated"] += 1
            else:
                product = kit.create_product(leaf_ids)
                product_id = _s(product.get("id"))
                if not product_id:
                    raise KitExportError("KIT create product returned no id")
                media = []
                for url in source_images:
                    uploaded = kit.upload_image_url(url)
                    file_id = _s(uploaded.get("id"))
                    if not file_id:
                        raise KitExportError("KIT image upload returned no id for %s" % url)
                    media.append({"type": "IMAGE", "display_sequence": len(media), "image_id": file_id})
                    stats["images_uploaded"] += 1
                body = {
                    "sku": supplier_sku,
                    "name": desired.get("name") or supplier_sku,
                    "brand": desired.get("brand") or "",
                    "status": "PUBLISHED",
                    "product_id": product_id,
                    "stocks": [
                        {"warehouse_id": msk_id, "quantity": quantity, "reserved": 0},
                        {"warehouse_id": spb_id, "quantity": quantity, "reserved": 0},
                    ],
                    "characteristics": _characteristic_payload(desired, characteristic_ids),
                }
                if pricing:
                    body["pricing"] = pricing
                variant = kit.create_variant(body)
                variant_id = _s(variant.get("id"))
                if not variant_id:
                    raise KitExportError("KIT create variant returned no id")
                if media:
                    kit.patch_variant(variant_id, {"media": media})
                variant = kit.get_variant(variant_id)
                variants_by_sku[supplier_sku] = [variant]
                stats["created"] += 1

            kit_id = variant.get("kit_id")
            if kit_id in (None, "") or not str(kit_id).isdigit():
                raise KitExportError("KIT numeric kit_id is missing for %s" % supplier_sku)
            image_urls = kit.public_image_urls(variant.get("media") or [])
            _set_webasyst_kit_data(wa, item["product_id"], kit_id, image_urls)
            stats["webasyst_backfilled"] += 1
        except Exception as exc:
            stats["errors"].append({"supplier_sku": supplier_sku, "error": str(exc)[:1000]})

    if stats["errors"]:
        stats["status"] = "partial_failure"
        raise KitExportError("KIT export completed with %d errors" % len(stats["errors"]), stats)
    return stats
