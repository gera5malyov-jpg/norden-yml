import mimetypes
import os
import re
import time
import unicodedata
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlparse

import requests

from webasyst.client import WebasystClient


KIT_BASE = "https://api.kit.yandex.net"


class KitSyncError(RuntimeError):
    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report or {}


def _s(value):
    return str(value or "").strip()


def _norm(value):
    return re.sub(r"[^0-9a-zа-яё]+", "", unicodedata.normalize("NFKC", _s(value)).casefold())


def _dec(value):
    try:
        return Decimal(str(value).replace("\xa0", "").replace(" ", "").replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _money(value):
    d = _dec(value)
    if d is None:
        return None
    return format(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _quantity(value):
    d = _dec(value)
    if d is None or d <= 0:
        return 0
    return int(d)


def _price_pair(purchase):
    p = _dec(purchase)
    if p is None or p <= 0:
        return None
    return {
        "price": _money(p * Decimal("1.60")),
        "manual_discount_price": _money(p * Decimal("1.25")),
    }


def _feature_kit_id(features):
    for feature in features or []:
        if _s(feature.get("code")).casefold() != "kit_id":
            continue
        for value in feature.get("values") or []:
            value = _s(value)
            if value.isdigit():
                return value
    return ""


def _feature_values(features):
    out = []
    for feature in features or []:
        code = _s(feature.get("code"))
        title = _s(feature.get("name"))
        if not title or code.casefold() == "kit_id":
            continue
        values = []
        for value in feature.get("values") or []:
            value = _s(value)
            if value and value not in values:
                values.append(value)
        if values:
            out.append((title, values))
    return out


class KitClient:
    def __init__(self, token=None, min_request_interval=0.42):
        token = _s(token or os.environ.get("YANDEX_KIT_TOKEN"))
        if not token:
            raise KitSyncError("YANDEX_KIT_TOKEN is not configured")
        self.session = requests.Session()
        self.headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        self.min_request_interval = float(min_request_interval)
        self._last_request = 0.0

    def _pace(self):
        delay = self.min_request_interval - (time.monotonic() - self._last_request)
        if delay > 0:
            time.sleep(delay)
        self._last_request = time.monotonic()

    def request(self, method, path, *, params=None, body=None, files=None, content_type=None, timeout=120):
        url = KIT_BASE + path
        last = None
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
            last = response
            if response.status_code == 429:
                time.sleep(float(response.headers.get("Retry-After") or min(45, 3 * (attempt + 1))))
                continue
            if response.status_code >= 500:
                time.sleep(min(20, 2 ** attempt))
                continue
            if response.status_code >= 400:
                raise KitSyncError("KIT HTTP %s %s: %s" % (
                    response.status_code,
                    path,
                    response.text[:800],
                ))
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError:
                return {"raw": response.text}
        raise KitSyncError("KIT retries exhausted for %s %s: %s" % (
            method,
            path,
            getattr(last, "status_code", ""),
        ))

    @staticmethod
    def _items(payload):
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        for key in ("items", "results", "variants", "warehouses", "categories", "characteristics", "products"):
            if isinstance(payload.get(key), list):
                return payload[key]
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "results"):
                if isinstance(data.get(key), list):
                    return data[key]
        return []

    @staticmethod
    def _total(payload):
        if not isinstance(payload, dict):
            return None
        for key in ("total", "total_count"):
            if isinstance(payload.get(key), int):
                return payload[key]
        meta = payload.get("meta")
        if isinstance(meta, dict):
            for key in ("total", "total_count"):
                if isinstance(meta.get(key), int):
                    return meta[key]
        return None

    def iter_collection(self, path, params=None):
        page = 1
        seen = 0
        while True:
            query = dict(params or {})
            query.update({"page": page, "per_page": 100})
            payload = self.request("GET", path, params=query)
            rows = self._items(payload)
            for row in rows:
                if isinstance(row, dict):
                    seen += 1
                    yield row
            total = self._total(payload)
            if not rows or (total is not None and seen >= total) or (total is None and len(rows) < 100):
                break
            page += 1

    def warehouses(self):
        return list(self.iter_collection("/v1/warehouses", {"status": "ACTIVE"}))

    def categories(self):
        return list(self.iter_collection("/v1/categories", {"status": ["ACTIVE"]}))

    def characteristics(self):
        return list(self.iter_collection("/v1/characteristics", {"status": ["ACTIVE"]}))

    def variants(self):
        return list(self.iter_collection("/v1/variants"))

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
        )

    def create_variant(self, body):
        return self.request("POST", "/v1/variants", body=body)

    def patch_variant(self, variant_id, body):
        return self.request(
            "PATCH",
            "/v1/variants/%s" % variant_id,
            body=body,
            content_type="application/merge-patch+json",
        )

    def get_variant(self, variant_id):
        return self.request("GET", "/v1/variants/%s" % variant_id)

    def upload_image_url(self, url):
        response = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        name = os.path.basename(urlparse(url).path) or "image.jpg"
        mime = response.headers.get("Content-Type") or mimetypes.guess_type(name)[0] or "image/jpeg"
        return self.request(
            "POST",
            "/v1/files",
            files={"file": (name, response.content, mime)},
            timeout=180,
        )

    def file_url(self, file_id):
        payload = self.request("GET", "/v1/files/%s" % file_id)
        return _s(payload.get("url")) if isinstance(payload, dict) else ""


def _warehouse_ids(kit):
    by_name = {}
    for row in kit.warehouses():
        name = _norm(row.get("title") or row.get("name"))
        if name:
            by_name[name] = _s(row.get("id"))
    required = {}
    for name in ("МСК", "СПБ привозной"):
        wid = by_name.get(_norm(name))
        if not wid:
            raise KitSyncError("KIT warehouse not found: %s" % name)
        required[name] = wid
    return required


def _category_index(rows):
    index = defaultdict(list)
    for row in rows:
        title = _s(row.get("title") or row.get("name"))
        if not title:
            continue
        index[(_s(row.get("parent_id")), _norm(title))].append(row)
    return index


def _ensure_category_path(kit, categories, index, path):
    parent = ""
    for title in path:
        key = (parent, _norm(title))
        matches = index.get(key, [])
        if len(matches) > 1:
            raise KitSyncError("KIT category is ambiguous: %s" % " > ".join(path))
        if len(matches) == 1:
            row = matches[0]
        else:
            row = kit.create_category(title, parent or None)
            if not _s(row.get("id")):
                raise KitSyncError("KIT did not return category id for %s" % title)
            categories.append(row)
            index[key].append(row)
        parent = _s(row.get("id"))
    return parent


def _webasyst_category_paths(categories, category_ids):
    by_id = {str(row.get("id")): row for row in categories if row.get("id") is not None}
    out = []
    for raw_id in category_ids or []:
        cid = str(raw_id)
        row = by_id.get(cid)
        if not row:
            continue
        path = []
        seen = set()
        while row and str(row.get("id")) not in seen:
            seen.add(str(row.get("id")))
            title = _s(row.get("name") or row.get("title"))
            if title:
                path.append(title)
            parent = _s(row.get("parent_id"))
            row = by_id.get(parent) if parent and parent != "0" else None
        path.reverse()
        if path and path not in out:
            out.append(path)
    return out


def _characteristic_index(rows):
    index = defaultdict(list)
    for row in rows:
        title = _s(row.get("title") or row.get("name"))
        if title:
            index[_norm(title)].append(row)
    return index


def _ensure_characteristic(kit, rows, index, title):
    key = _norm(title)
    matches = index.get(key, [])
    exact = [row for row in matches if _s(row.get("title") or row.get("name")) == title]
    if len(exact) == 1:
        return _s(exact[0].get("id"))
    if len(exact) > 1 or len(matches) > 1:
        raise KitSyncError("KIT characteristic is ambiguous: %s" % title)
    if len(matches) == 1:
        return _s(matches[0].get("id"))
    row = kit.create_characteristic(title)
    cid = _s(row.get("id"))
    if not cid:
        raise KitSyncError("KIT did not return characteristic id: %s" % title)
    rows.append(row)
    index[key].append(row)
    return cid


SUPPLIER_ARTICLE_CHARACTERISTIC = "Артикул поставщика"
LEGACY_CODE_SITE_CHARACTERISTIC = "Код для сайта"


def _kit_characteristics(
    kit,
    features,
    rows,
    index,
    supplier_article_id=None,
    legacy_code_site_id=None,
    supplier_sku="",
):
    out = []
    seen_titles = set()
    for title, values in _feature_values(features):
        normalized_title = _norm(title)
        if normalized_title in {
            _norm(SUPPLIER_ARTICLE_CHARACTERISTIC),
            _norm(LEGACY_CODE_SITE_CHARACTERISTIC),
        }:
            continue
        cid = _ensure_characteristic(kit, rows, index, title)
        out.append({
            "characteristic_id": cid,
            "value": values[0],
            "values": values,
        })
        seen_titles.add(normalized_title)
    supplier_sku = _s(supplier_sku)
    if supplier_article_id and supplier_sku:
        out.append({
            "characteristic_id": supplier_article_id,
            "value": supplier_sku,
            "values": [supplier_sku],
        })
    if legacy_code_site_id and supplier_sku and legacy_code_site_id != supplier_article_id:
        out.append({
            "characteristic_id": legacy_code_site_id,
            "value": supplier_sku,
            "values": [supplier_sku],
        })
    return out


def _variant_characteristic_values(variant, characteristic_id):
    characteristic_id = _s(characteristic_id)
    if not characteristic_id:
        return []
    for item in variant.get("characteristics") or []:
        if not isinstance(item, dict):
            continue
        if _s(item.get("characteristic_id")) != characteristic_id:
            continue
        values = []
        raw_values = item.get("values") or []
        if not isinstance(raw_values, list):
            raw_values = [raw_values]
        for value in raw_values:
            value = _s(value)
            if value and value not in values:
                values.append(value)
        fallback = _s(item.get("value"))
        if fallback and fallback not in values:
            values.append(fallback)
        return values
    return []


def _variant_supplier_article(variant, supplier_identity_ids):
    values = []
    for characteristic_id in supplier_identity_ids or []:
        for value in _variant_characteristic_values(variant, characteristic_id):
            if value and value not in values:
                values.append(value)
    if len(values) > 1:
        raise KitSyncError(
            "KIT variant %s has conflicting supplier articles: %s"
            % (_s(variant.get("id")), ", ".join(values))
        )
    return values[0] if values else ""


def _variant_indexes(variants, supplier_identity_ids):
    by_sku = defaultdict(list)
    by_kit_id = defaultdict(list)
    by_supplier_brand = defaultdict(list)
    for row in variants:
        sku = _s(row.get("sku"))
        kit_id = _s(row.get("kit_id"))
        supplier_article = _variant_supplier_article(row, supplier_identity_ids)
        brand_key = _norm(row.get("brand"))
        if sku:
            by_sku[sku].append(row)
        if kit_id:
            by_kit_id[kit_id].append(row)
        if supplier_article and brand_key:
            by_supplier_brand[(supplier_article, brand_key)].append(row)
    return by_sku, by_kit_id, by_supplier_brand


def _select_variant(product, by_sku, by_kit_id, by_supplier_brand, supplier_identity_ids, brand):
    expected_kit_id = _feature_kit_id(product.get("features"))
    supplier_sku = _s(product.get("supplier_sku"))
    sku = _s(product.get("sku"))
    brand_key = _norm(brand)

    if expected_kit_id:
        matches = by_kit_id.get(expected_kit_id, [])
        if len(matches) > 1:
            raise KitSyncError("KIT ID %s matches %d variants" % (expected_kit_id, len(matches)))
        if not matches:
            raise KitSyncError(
                "KIT ID %s is stored in Webasyst but was not found in KIT; automatic creation is blocked"
                % expected_kit_id
            )
        variant = matches[0]
        existing_brand = _norm(variant.get("brand"))
        if existing_brand and brand_key and existing_brand != brand_key:
            raise KitSyncError(
                "KIT ID %s brand conflict: KIT=%s, Webasyst=%s"
                % (expected_kit_id, _s(variant.get("brand")), brand)
            )
        existing_supplier = _variant_supplier_article(variant, supplier_identity_ids)
        if existing_supplier and supplier_sku and existing_supplier != supplier_sku:
            raise KitSyncError(
                "KIT ID %s supplier article conflict: KIT=%s, Webasyst=%s"
                % (expected_kit_id, existing_supplier, supplier_sku)
            )
        return variant

    if not sku or not supplier_sku or not brand_key:
        raise KitSyncError(
            "First KIT match requires exact Webasyst SKU + supplier article + brand"
        )

    sku_matches = by_sku.get(sku, [])
    exact = []
    for variant in sku_matches:
        if _norm(variant.get("brand")) != brand_key:
            continue
        if _variant_supplier_article(variant, supplier_identity_ids) != supplier_sku:
            continue
        exact.append(variant)

    if len(exact) > 1:
        raise KitSyncError(
            "Exact KIT match is ambiguous for SKU=%s, supplier=%s, brand=%s"
            % (sku, supplier_sku, brand)
        )
    if len(exact) == 1:
        return exact[0]

    if sku_matches:
        raise KitSyncError(
            "KIT already contains SKU=%s but supplier article/brand do not match exactly; automatic linking is blocked"
            % sku
        )

    supplier_matches = by_supplier_brand.get((supplier_sku, brand_key), [])
    if supplier_matches:
        raise KitSyncError(
            "KIT already contains supplier article=%s and brand=%s under another SKU; automatic creation is blocked"
            % (supplier_sku, brand)
        )

    return None


def _media_from_source(kit, urls):
    media = []
    for url in list(dict.fromkeys(_s(x) for x in (urls or []) if _s(x)))[:20]:
        upload = kit.upload_image_url(url)
        fid = _s(upload.get("id"))
        if not fid:
            raise KitSyncError("KIT image upload returned no file id for %s" % url)
        media.append({
            "type": "IMAGE",
            "display_sequence": len(media),
            "image_id": fid,
        })
    return media


def _public_media_urls(kit, variant):
    media = [
        row for row in (variant.get("media") or [])
        if isinstance(row, dict) and _s(row.get("type")).upper() == "IMAGE" and _s(row.get("image_id"))
    ]
    media.sort(key=lambda row: int(row.get("display_sequence") or 0))
    out = []
    for row in media:
        url = kit.file_url(_s(row.get("image_id")))
        if url and url not in out:
            out.append(url)
    return out


def _summary(urls):
    urls = [x for x in (_s(url) for url in urls or []) if x]
    if not urls:
        return ""
    return "[extimg]\n" + "\n".join(urls) + "\n[/extimg]"


def sync_manifest(manifest, config, *, kit=None, wa=None):
    rules = config.get("rules") or {}
    if not rules.get("export_to_kit", False):
        return {"status": "disabled", "eligible": 0, "created": 0, "updated": 0, "skipped_no_category": 0, "errors": []}

    kit = kit or KitClient()
    wa = wa or WebasystClient()
    brand = _s((config.get("identity") or {}).get("brand"))
    products = [row for row in (manifest.get("items") or []) if isinstance(row, dict)]
    wa_categories = [row for row in (manifest.get("categories") or []) if isinstance(row, dict)]

    eligible = [row for row in products if row.get("category_ids")]
    report = {
        "status": "ok",
        "eligible": len(eligible),
        "created": 0,
        "updated": 0,
        "skipped_no_category": len(products) - len(eligible),
        "categories_created": 0,
        "characteristics_created": 0,
        "images_uploaded": 0,
        "webasyst_updated": 0,
        "errors": [],
    }
    if not eligible:
        return report

    warehouses = _warehouse_ids(kit)
    kit_categories = kit.categories()
    category_index = _category_index(kit_categories)
    kit_characteristics = kit.characteristics()
    characteristic_index = _characteristic_index(kit_characteristics)
    supplier_article_id = _ensure_characteristic(
        kit,
        kit_characteristics,
        characteristic_index,
        SUPPLIER_ARTICLE_CHARACTERISTIC,
    )
    legacy_matches = characteristic_index.get(_norm(LEGACY_CODE_SITE_CHARACTERISTIC), [])
    if len(legacy_matches) > 1:
        raise KitSyncError(
            "KIT characteristic is ambiguous: %s" % LEGACY_CODE_SITE_CHARACTERISTIC
        )
    legacy_code_site_id = _s(legacy_matches[0].get("id")) if legacy_matches else ""
    supplier_identity_ids = [
        value for value in (supplier_article_id, legacy_code_site_id) if value
    ]
    variants = kit.variants()
    by_sku, by_kit_id, by_supplier_brand = _variant_indexes(
        variants,
        supplier_identity_ids,
    )

    category_count_before = len(kit_categories)
    characteristic_count_before = len(kit_characteristics)

    for product in eligible:
        try:
            paths = _webasyst_category_paths(wa_categories, product.get("category_ids"))
            if not paths:
                report["skipped_no_category"] += 1
                continue
            category_ids = []
            for path in paths:
                cid = _ensure_category_path(kit, kit_categories, category_index, path)
                if cid and cid not in category_ids:
                    category_ids.append(cid)
            if not category_ids:
                raise KitSyncError("No KIT category resolved")

            variant = _select_variant(
                product,
                by_sku,
                by_kit_id,
                by_supplier_brand,
                supplier_identity_ids,
                brand,
            )
            qty = _quantity(product.get("stock"))
            stocks = [
                {"warehouse_id": warehouses["МСК"], "quantity": qty, "reserved": 0},
                {"warehouse_id": warehouses["СПБ привозной"], "quantity": qty, "reserved": 0},
            ]
            pricing = _price_pair(product.get("purchase_price"))
            characteristics = _kit_characteristics(
                kit,
                product.get("features"),
                kit_characteristics,
                characteristic_index,
                supplier_article_id=supplier_article_id,
                legacy_code_site_id=legacy_code_site_id,
                supplier_sku=product.get("supplier_sku"),
            )
            variant_payload = {
                "sku": _s(product.get("sku")),
                "name": _s(product.get("name")),
                "description": _s(product.get("description")),
                "brand": brand,
                "status": "PUBLISHED" if int(product.get("status") or 0) else "HIDDEN",
                "stocks": stocks,
                "characteristics": characteristics,
            }
            if pricing:
                variant_payload["pricing"] = pricing

            if variant is None:
                created_product = kit.create_product(category_ids)
                product_id = _s(created_product.get("id"))
                if not product_id:
                    raise KitSyncError("KIT product create returned no id")
                variant_payload["product_id"] = product_id
                created_variant = kit.create_variant(variant_payload)
                variant_id = _s(created_variant.get("id"))
                if not variant_id:
                    raise KitSyncError("KIT variant create returned no id")
                report["created"] += 1
                variant = dict(created_variant)
                variant["id"] = variant_id
                by_sku[_s(product.get("sku"))].append(variant)
                by_supplier_brand[(_s(product.get("supplier_sku")), _norm(brand))].append(variant)
            else:
                variant_id = _s(variant.get("id"))
                product_id = _s(variant.get("product_id"))
                if not variant_id or not product_id:
                    full = kit.get_variant(variant_id)
                    product_id = _s(full.get("product_id"))
                if not product_id:
                    raise KitSyncError("Existing KIT variant has no product_id")
                kit.patch_product(product_id, category_ids)
                kit.patch_variant(variant_id, variant_payload)
                report["updated"] += 1

            full = kit.get_variant(variant_id)
            current_media = [
                row for row in (full.get("media") or [])
                if isinstance(row, dict) and _s(row.get("type")).upper() == "IMAGE"
            ]
            if not current_media and product.get("image_urls"):
                media = _media_from_source(kit, product.get("image_urls"))
                if media:
                    kit.patch_variant(variant_id, {"media": media})
                    report["images_uploaded"] += len(media)
                    full = kit.get_variant(variant_id)

            kit_id = _s(full.get("kit_id"))
            if not kit_id or not kit_id.isdigit():
                raise KitSyncError("KIT variant %s has no numeric kit_id" % variant_id)
            public_urls = _public_media_urls(kit, full)

            web_update = {"features": {"kit_id": kit_id}}
            if public_urls:
                web_update["summary"] = _summary(public_urls)
            wa.call(
                "shop.product.update",
                http_method="POST",
                params={"id": str(product["product_id"])},
                data=web_update,
            )
            report["webasyst_updated"] += 1

            if _s(product.get("sku")):
                by_sku[_s(product.get("sku"))] = [full]
            by_kit_id[kit_id] = [full]
            supplier_key = (_s(product.get("supplier_sku")), _norm(brand))
            if supplier_key[0] and supplier_key[1]:
                by_supplier_brand[supplier_key] = [full]
        except Exception as exc:
            report["errors"].append({
                "product_id": product.get("product_id"),
                "supplier_sku": product.get("supplier_sku"),
                "sku": product.get("sku"),
                "error": str(exc)[:1200],
            })

    report["categories_created"] = max(0, len(kit_categories) - category_count_before)
    report["characteristics_created"] = max(0, len(kit_characteristics) - characteristic_count_before)
    if report["errors"]:
        report["status"] = "partial_failure"
    return report
