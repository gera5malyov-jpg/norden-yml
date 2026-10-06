import concurrent.futures
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
        return list(self.scan_all_variants_parallel(workers=6))

    def scan_all_variants_parallel(self, workers=6):
        first = self.request("GET", "/v1/variants", params={"page": 1, "per_page": 100})
        first_rows = [row for row in self._items(first) if isinstance(row, dict)]
        for row in first_rows:
            yield row
        total = self._total(first)
        if total is None:
            # Some KIT responses omit total. Continue safely page-by-page
            # instead of silently treating the first 100 rows as the catalog.
            page = 2
            while len(first_rows) == 100:
                payload = self.request(
                    "GET",
                    "/v1/variants",
                    params={"page": page, "per_page": 100},
                )
                rows = [row for row in self._items(payload) if isinstance(row, dict)]
                for row in rows:
                    yield row
                if len(rows) < 100:
                    return
                first_rows = rows
                page += 1
            return
        if total <= len(first_rows):
            return
        pages = (int(total) + 99) // 100

        def get_page(page):
            headers = dict(self.headers)
            last_error = None
            for attempt in range(12):
                try:
                    response = requests.get(
                        KIT_BASE + "/v1/variants",
                        headers=headers,
                        params={"page": page, "per_page": 100},
                        timeout=120,
                    )
                    if response.status_code == 429:
                        last_error = RuntimeError("KIT variant scan HTTP 429")
                        time.sleep(float(response.headers.get("Retry-After") or min(15, 1 + attempt)))
                        continue
                    if response.status_code >= 500 or response.status_code == 400:
                        last_error = RuntimeError("KIT variant scan HTTP %s" % response.status_code)
                        time.sleep(min(15, 1 + attempt * 2))
                        continue
                    if response.status_code >= 400:
                        raise KitSyncError(
                            "KIT HTTP %s /v1/variants: %s"
                            % (response.status_code, response.text[:500])
                        )
                    payload = response.json()
                    return page, [row for row in self._items(payload) if isinstance(row, dict)]
                except (requests.RequestException, ValueError) as exc:
                    last_error = exc
                    if attempt + 1 < 12:
                        time.sleep(min(15, 1 + attempt * 2))
                        continue
            raise KitSyncError(
                "KIT variant scan page %s retries exhausted: %s" % (page, last_error)
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
            futures = [pool.submit(get_page, page) for page in range(2, pages + 1)]
            done = 1
            for future in concurrent.futures.as_completed(futures):
                page, rows = future.result()
                done += 1
                if done % 50 == 0 or done == pages:
                    print("KIT bulk scan: %d/%d pages" % (done, pages), flush=True)
                for row in rows:
                    yield row

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


def _characteristic_sort_key(row):
    value = _s(row.get("id"))
    return (0, int(value)) if value.isdigit() else (1, value)


def _pick_characteristic(matches, title):
    exact = [
        row for row in matches
        if _s(row.get("title") or row.get("name")) == title
    ]
    pool = exact or list(matches)
    if not pool:
        return None
    # Historical KIT data can contain duplicate characteristic definitions.
    # Reuse one stable existing definition instead of creating more duplicates.
    return sorted(pool, key=_characteristic_sort_key)[0]


def _single_characteristic_id(index, title):
    row = _pick_characteristic(index.get(_norm(title), []), title)
    return _s(row.get("id")) if row else ""


def _ensure_characteristic(kit, rows, index, title):
    key = _norm(title)
    row = _pick_characteristic(index.get(key, []), title)
    if row:
        return _s(row.get("id"))
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


def _variant_supplier_articles(variant, supplier_identity_ids):
    values = []
    for characteristic_id in supplier_identity_ids or []:
        for value in _variant_characteristic_values(variant, characteristic_id):
            if value and value not in values:
                values.append(value)
    return values


def _variant_supplier_article(variant, supplier_identity_ids):
    values = _variant_supplier_articles(variant, supplier_identity_ids)
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
        brand_key = _norm(row.get("brand"))
        if sku:
            by_sku[sku].append(row)
        if kit_id:
            by_kit_id[kit_id].append(row)
        if brand_key:
            # Index every historical supplier identity value. Corrupt rows are
            # rejected only when selected for the current Webasyst product.
            for supplier_article in _variant_supplier_articles(row, supplier_identity_ids):
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

    # SKU is the primary stable identity. One same-brand KIT row is safe to
    # relink even when its historical supplier-article field is empty/stale.
    same_brand = [
        row for row in sku_matches
        if _norm(row.get("brand")) == brand_key
    ]
    if len(same_brand) == 1:
        _variant_supplier_article(same_brand[0], supplier_identity_ids)
        return same_brand[0]
    if len(same_brand) > 1:
        raise KitSyncError(
            "KIT SKU=%s has %d Norden variants; automatic linking is ambiguous"
            % (sku, len(same_brand))
        )

    if sku_matches:
        candidates = []
        for row in sku_matches[:5]:
            articles = _variant_supplier_articles(row, supplier_identity_ids)
            candidates.append(
                "id=%s kit_id=%s brand=%s supplier=%s"
                % (
                    _s(row.get("id")),
                    _s(row.get("kit_id")),
                    _s(row.get("brand")),
                    ",".join(articles) or "-",
                )
            )
        raise KitSyncError(
            "KIT already contains SKU=%s but no candidate has brand=%s; "
            "automatic linking is blocked; candidates: %s"
            % (sku, brand, " | ".join(candidates))
        )

    supplier_matches = by_supplier_brand.get((supplier_sku, brand_key), [])
    if supplier_matches:
        raise KitSyncError(
            "KIT already contains supplier article=%s and brand=%s under another SKU; automatic creation is blocked"
            % (supplier_sku, brand)
        )

    return None


def _identity_preflight(products, variants, characteristic_index, brand):
    supplier_article_id = _single_characteristic_id(
        characteristic_index,
        SUPPLIER_ARTICLE_CHARACTERISTIC,
    )
    legacy_code_site_id = _single_characteristic_id(
        characteristic_index,
        LEGACY_CODE_SITE_CHARACTERISTIC,
    )
    supplier_identity_ids = [
        value for value in (supplier_article_id, legacy_code_site_id) if value
    ]

    try:
        by_sku, by_kit_id, by_supplier_brand = _variant_indexes(
            variants,
            supplier_identity_ids,
        )
    except Exception as exc:
        return {
            "status": "blocked",
            "matched_by_kit_id": 0,
            "matched_exact_identity": 0,
            "matched_by_sku_brand": 0,
            "create_new": 0,
            "conflicts": 1,
            "conflict_sample": [{"error": str(exc)[:1200]}],
        }, None

    report = {
        "status": "ok",
        "matched_by_kit_id": 0,
        "matched_exact_identity": 0,
        "matched_by_sku_brand": 0,
        "create_new": 0,
        "conflicts": 0,
        "conflict_sample": [],
    }
    selected = {}
    for product in products:
        key = str(product.get("product_id") or product.get("sku_id") or product.get("supplier_sku") or "")
        try:
            variant = _select_variant(
                product,
                by_sku,
                by_kit_id,
                by_supplier_brand,
                supplier_identity_ids,
                brand,
            )
            selected[key] = variant
            if variant is None:
                report["create_new"] += 1
            elif _feature_kit_id(product.get("features")):
                report["matched_by_kit_id"] += 1
            else:
                existing_supplier = _variant_supplier_article(
                    variant,
                    supplier_identity_ids,
                )
                if existing_supplier == _s(product.get("supplier_sku")):
                    report["matched_exact_identity"] += 1
                else:
                    report["matched_by_sku_brand"] += 1
        except Exception as exc:
            report["conflicts"] += 1
            if len(report["conflict_sample"]) < 100:
                report["conflict_sample"].append({
                    "product_id": product.get("product_id"),
                    "supplier_sku": product.get("supplier_sku"),
                    "sku": product.get("sku"),
                    "kit_id": _feature_kit_id(product.get("features")),
                    "error": str(exc)[:1200],
                })

    if report["conflicts"]:
        report["status"] = "blocked"
        return report, None

    return report, {
        "supplier_article_id": supplier_article_id,
        "legacy_code_site_id": legacy_code_site_id,
        "supplier_identity_ids": supplier_identity_ids,
        "by_sku": by_sku,
        "by_kit_id": by_kit_id,
        "by_supplier_brand": by_supplier_brand,
        "selected": selected,
    }


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



def _plan_category_paths_readonly(categories, paths):
    index = _category_index(categories)
    required = []
    missing = []
    seen_required = set()
    seen_missing = set()

    for path in paths or []:
        clean = [_s(title) for title in path if _s(title)]
        if not clean:
            continue
        label = " > ".join(clean)
        if label not in seen_required:
            required.append(label)
            seen_required.add(label)

        parent = ""
        unresolved = False
        for depth, title in enumerate(clean):
            key = (parent, _norm(title))
            matches = index.get(key, [])
            if len(matches) > 1:
                raise KitSyncError("KIT category is ambiguous: %s" % label)
            if len(matches) == 1:
                parent = _s(matches[0].get("id"))
                continue
            unresolved = True
            missing_label = " > ".join(clean[: depth + 1])
            if missing_label not in seen_missing:
                missing.append(missing_label)
                seen_missing.add(missing_label)
            # Once a parent is missing, every deeper node is necessarily
            # missing too; record the remaining full path once.
            if depth + 1 < len(clean) and label not in seen_missing:
                missing.append(label)
                seen_missing.add(label)
            break
        if unresolved:
            continue

    return required, missing


def plan_manifest(manifest, config, *, kit=None):
    """Read-only KIT preflight for the optimized bulk-sync path."""
    rules = config.get("rules") or {}
    if not rules.get("export_to_kit", False):
        return {
            "status": "disabled",
            "readonly": True,
            "eligible": 0,
            "skipped_no_category": 0,
            "items": [],
        }

    kit = kit or KitClient()
    brand = _s((config.get("identity") or {}).get("brand"))
    products = [row for row in (manifest.get("items") or []) if isinstance(row, dict)]
    wa_categories = [row for row in (manifest.get("categories") or []) if isinstance(row, dict)]
    eligible = [row for row in products if row.get("category_ids")]

    report = {
        "status": "ok",
        "readonly": True,
        "eligible": len(eligible),
        "skipped_no_category": len(products) - len(eligible),
        "required_category_paths": [],
        "missing_category_paths": [],
        "missing_characteristics": [],
        "would_create": 0,
        "would_update": 0,
        "would_upload_images": 0,
        "errors": [],
        "items": [],
    }
    if not eligible:
        return report

    # Everything below is GET/read-only. Variant discovery uses the same
    # bulk scanner as apply, so preflight no longer performs thousands of
    # one-SKU searches.
    _warehouse_ids(kit)
    kit_categories = kit.categories()
    kit_characteristics = kit.characteristics()
    characteristic_index = _characteristic_index(kit_characteristics)
    variants = kit.variants()

    preflight, identity = _identity_preflight(
        eligible,
        variants,
        characteristic_index,
        brand,
    )
    report["preflight"] = preflight
    if preflight.get("status") != "ok":
        report["status"] = "blocked"
        report["errors"].extend(list(preflight.get("conflict_sample") or []))

    all_paths = []
    product_paths = {}
    for product in eligible:
        key = _product_identity_key(product)
        paths = _webasyst_category_paths(
            wa_categories,
            product.get("category_ids"),
        )
        product_paths[key] = paths
        if not paths:
            report["errors"].append({
                "product_id": product.get("product_id"),
                "supplier_sku": product.get("supplier_sku"),
                "error": "Webasyst category path cannot be resolved",
            })
        else:
            all_paths.extend(paths)

    try:
        required, missing = _plan_category_paths_readonly(
            kit_categories,
            all_paths,
        )
        report["required_category_paths"] = required
        report["missing_category_paths"] = missing
    except Exception as exc:
        report["status"] = "blocked"
        report["errors"].append({"error": str(exc)[:1200]})

    wanted_characteristics = {SUPPLIER_ARTICLE_CHARACTERISTIC}
    for product in eligible:
        for title, values in _feature_values(product.get("features")):
            if values and _norm(title) not in {
                _norm(SUPPLIER_ARTICLE_CHARACTERISTIC),
                _norm(LEGACY_CODE_SITE_CHARACTERISTIC),
            }:
                wanted_characteristics.add(title)

    for title in sorted(wanted_characteristics):
        cid = _single_characteristic_id(characteristic_index, title)
        if not cid:
            report["missing_characteristics"].append(title)

    selected = identity.get("selected", {}) if identity else {}
    for product in eligible:
        key = _product_identity_key(product)
        variant = selected.get(key) if identity else None
        action = (
            "blocked"
            if preflight.get("status") != "ok"
            else ("create" if variant is None else "update")
        )
        if action == "create":
            report["would_create"] += 1
        elif action == "update":
            report["would_update"] += 1

        source_images = list(dict.fromkeys(
            _s(url) for url in (product.get("image_urls") or []) if _s(url)
        ))[:20]
        current_media = []
        if isinstance(variant, dict):
            current_media = [
                row for row in (variant.get("media") or [])
                if isinstance(row, dict)
                and _s(row.get("type")).upper() == "IMAGE"
            ]
        image_uploads = len(source_images) if source_images and not current_media else 0
        report["would_upload_images"] += image_uploads
        report["items"].append({
            "product_id": product.get("product_id"),
            "supplier_sku": product.get("supplier_sku"),
            "webasyst_sku": product.get("sku"),
            "name": product.get("name"),
            "action": action,
            "kit_id": _s(variant.get("kit_id")) if isinstance(variant, dict) else "",
            "categories": [
                " > ".join(path)
                for path in product_paths.get(key, [])
            ],
            "source_images": len(source_images),
            "image_uploads": image_uploads,
        })

    if report["errors"] and report["status"] == "ok":
        report["status"] = "blocked"
    return report


def _product_identity_key(product):
    return str(
        product.get("product_id")
        or product.get("sku_id")
        or product.get("supplier_sku")
        or product.get("sku")
        or ""
    )


def _canonical_stocks(rows):
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        out.append((
            _s(row.get("warehouse_id")),
            _quantity(row.get("quantity")),
            _quantity(row.get("reserved")),
        ))
    return sorted(out)


def _canonical_characteristics(rows):
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        values = row.get("values") or []
        if not isinstance(values, list):
            values = [values]
        normalized = tuple(sorted(_s(value) for value in values if _s(value)))
        if not normalized and _s(row.get("value")):
            normalized = (_s(row.get("value")),)
        out.append((_s(row.get("characteristic_id")), normalized))
    return sorted(out)


def _variant_delta(current, desired):
    if not isinstance(current, dict):
        return dict(desired or {})
    desired = desired or {}
    out = {}
    for field in ("sku", "name", "description", "brand", "status"):
        if field in desired and _s(current.get(field)) != _s(desired.get(field)):
            out[field] = desired[field]
    if "stocks" in desired and _canonical_stocks(current.get("stocks")) != _canonical_stocks(desired.get("stocks")):
        out["stocks"] = desired["stocks"]
    if "characteristics" in desired and _canonical_characteristics(current.get("characteristics")) != _canonical_characteristics(desired.get("characteristics")):
        out["characteristics"] = desired["characteristics"]
    if "pricing" in desired:
        current_pricing = current.get("pricing") or {}
        desired_pricing = desired.get("pricing") or {}
        if (
            _money(current_pricing.get("price")) != _money(desired_pricing.get("price"))
            or _money(current_pricing.get("manual_discount_price")) != _money(desired_pricing.get("manual_discount_price"))
        ):
            out["pricing"] = desired_pricing
    return out


def sync_manifest(manifest, config, *, kit=None, wa=None):
    rules = config.get("rules") or {}
    if not rules.get("export_to_kit", False):
        return {
            "status": "disabled",
            "eligible": 0,
            "created": 0,
            "updated": 0,
            "skipped_no_category": 0,
            "errors": [],
        }

    kit = kit or KitClient()
    wa = wa or WebasystClient()
    use_parallel = isinstance(kit, KitClient) and isinstance(wa, WebasystClient)
    brand = _s((config.get("identity") or {}).get("brand"))
    products = [row for row in (manifest.get("items") or []) if isinstance(row, dict)]
    wa_categories = [row for row in (manifest.get("categories") or []) if isinstance(row, dict)]

    eligible = [row for row in products if row.get("category_ids")]
    report = {
        "status": "ok",
        "eligible": len(eligible),
        "created": 0,
        "updated": 0,
        "unchanged_variants": 0,
        "skipped_no_category": len(products) - len(eligible),
        "categories_created": 0,
        "characteristics_created": 0,
        "images_uploaded": 0,
        "webasyst_updated": 0,
        "webasyst_kit_id_skipped": 0,
        "image_summary_updated": 0,
        "errors": [],
    }
    if not eligible:
        return report

    # One bulk read builds the complete identity index before writes.
    warehouses = _warehouse_ids(kit)
    kit_characteristics = kit.characteristics()
    characteristic_index = _characteristic_index(kit_characteristics)
    variants = kit.variants()
    preflight, identity = _identity_preflight(
        eligible,
        variants,
        characteristic_index,
        brand,
    )
    report["preflight"] = preflight
    if preflight.get("status") != "ok":
        report["status"] = "blocked"
        report["errors"] = list(preflight.get("conflict_sample") or [])
        return report

    supplier_article_id = identity["supplier_article_id"]
    legacy_code_site_id = identity["legacy_code_site_id"]
    if not supplier_article_id:
        supplier_article_id = _ensure_characteristic(
            kit,
            kit_characteristics,
            characteristic_index,
            SUPPLIER_ARTICLE_CHARACTERISTIC,
        )
    supplier_identity_ids = [
        value for value in (supplier_article_id, legacy_code_site_id) if value
    ]

    kit_categories = kit.categories()
    category_index = _category_index(kit_categories)
    category_count_before = len(kit_categories)
    characteristic_count_before = len(kit_characteristics)

    # Resolve all categories and characteristic definitions before parallel
    # product writes. This avoids duplicate category/feature creation races.
    category_ids_by_key = {}
    for product in eligible:
        key = _product_identity_key(product)
        paths = _webasyst_category_paths(wa_categories, product.get("category_ids"))
        category_ids = []
        for path in paths:
            cid = _ensure_category_path(kit, kit_categories, category_index, path)
            if cid and cid not in category_ids:
                category_ids.append(cid)
        category_ids_by_key[key] = category_ids

    for product in eligible:
        for title, values in _feature_values(product.get("features")):
            if _norm(title) in {
                _norm(SUPPLIER_ARTICLE_CHARACTERISTIC),
                _norm(LEGACY_CODE_SITE_CHARACTERISTIC),
            }:
                continue
            if values:
                _ensure_characteristic(
                    kit,
                    kit_characteristics,
                    characteristic_index,
                    title,
                )

    selected_variants = identity["selected"]

    def core_one(product):
        local_kit = KitClient() if use_parallel else kit
        local_wa = WebasystClient() if use_parallel else wa
        key = _product_identity_key(product)
        category_ids = category_ids_by_key.get(key) or []
        if not category_ids:
            raise KitSyncError("No KIT category resolved")

        variant = selected_variants.get(key)
        qty = _quantity(product.get("stock"))
        stocks = [
            {"warehouse_id": warehouses["МСК"], "quantity": qty, "reserved": 0},
            {"warehouse_id": warehouses["СПБ привозной"], "quantity": qty, "reserved": 0},
        ]
        pricing = _price_pair(product.get("purchase_price"))
        characteristics = _kit_characteristics(
            local_kit,
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

        created = False
        changed = False
        if variant is None:
            created_product = local_kit.create_product(category_ids)
            product_id = _s(created_product.get("id"))
            if not product_id:
                raise KitSyncError("KIT product create returned no id")
            variant_payload["product_id"] = product_id
            created_variant = local_kit.create_variant(variant_payload)
            variant_id = _s(created_variant.get("id"))
            if not variant_id:
                raise KitSyncError("KIT variant create returned no id")
            variant = dict(created_variant)
            variant["id"] = variant_id
            created = True
            changed = True
        else:
            variant_id = _s(variant.get("id"))
            product_id = _s(variant.get("product_id"))
            current = variant
            if not variant_id:
                raise KitSyncError("Existing KIT variant has no id")
            if not product_id:
                current = local_kit.get_variant(variant_id)
                product_id = _s(current.get("product_id"))
            if not product_id:
                raise KitSyncError("Existing KIT variant has no product_id")
            # Category writes stay explicit because category ids are not
            # consistently present in the variant collection response.
            local_kit.patch_product(product_id, category_ids)
            delta = _variant_delta(current, variant_payload)
            if delta:
                local_kit.patch_variant(variant_id, delta)
                changed = True

        full = local_kit.get_variant(variant_id)
        kit_id = _s(full.get("kit_id"))
        if not kit_id or not kit_id.isdigit():
            raise KitSyncError("KIT variant %s has no numeric kit_id" % variant_id)

        previous_kit_id = _feature_kit_id(product.get("features"))
        kit_id_written = False
        if previous_kit_id != kit_id:
            local_wa.call(
                "shop.product.update",
                http_method="POST",
                params={"id": str(product["product_id"])},
                data={"features": {"kit_id": kit_id}},
            )
            kit_id_written = True

        return {
            "product": product,
            "variant_id": variant_id,
            "kit_id": kit_id,
            "created": created,
            "changed": changed,
            "kit_id_written": kit_id_written,
            # New/relinked products need media reconciliation now. Existing
            # products with both KIT ID and summary can skip the expensive
            # file-url pass on ordinary delta updates.
            "needs_media": bool(product.get("image_urls")) and (
                created
                or previous_kit_id != kit_id
                or not _s(product.get("summary"))
            ),
        }

    core_results = []
    if use_parallel:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            future_map = {pool.submit(core_one, product): product for product in eligible}
            done = 0
            for future in concurrent.futures.as_completed(future_map):
                product = future_map[future]
                done += 1
                try:
                    result = future.result()
                    core_results.append(result)
                    if result["created"]:
                        report["created"] += 1
                    else:
                        report["updated"] += 1
                        if not result["changed"]:
                            report["unchanged_variants"] += 1
                    if result["kit_id_written"]:
                        report["webasyst_updated"] += 1
                    else:
                        report["webasyst_kit_id_skipped"] += 1
                except Exception as exc:
                    report["errors"].append({
                        "product_id": product.get("product_id"),
                        "supplier_sku": product.get("supplier_sku"),
                        "sku": product.get("sku"),
                        "stage": "core",
                        "error": str(exc)[:1200],
                    })
                if done % 100 == 0 or done == len(eligible):
                    print("KIT core sync: %d/%d products" % (done, len(eligible)), flush=True)
    else:
        for product in eligible:
            try:
                result = core_one(product)
                core_results.append(result)
                if result["created"]:
                    report["created"] += 1
                else:
                    report["updated"] += 1
                    if not result["changed"]:
                        report["unchanged_variants"] += 1
                if result["kit_id_written"]:
                    report["webasyst_updated"] += 1
                else:
                    report["webasyst_kit_id_skipped"] += 1
            except Exception as exc:
                report["errors"].append({
                    "product_id": product.get("product_id"),
                    "supplier_sku": product.get("supplier_sku"),
                    "sku": product.get("sku"),
                    "stage": "core",
                    "error": str(exc)[:1200],
                })

    # Media is deliberately second. All product cards, prices, stocks and KIT
    # IDs are visible before image transfer begins.
    media_queue = [row for row in core_results if row.get("needs_media")]

    def media_one(row):
        product = row["product"]
        local_kit = KitClient() if use_parallel else kit
        local_wa = WebasystClient() if use_parallel else wa
        variant_id = row["variant_id"]
        full = local_kit.get_variant(variant_id)
        current_media = [
            item for item in (full.get("media") or [])
            if isinstance(item, dict)
            and _s(item.get("type")).upper() == "IMAGE"
            and _s(item.get("image_id"))
        ]
        uploaded = 0
        if not current_media and product.get("image_urls"):
            media = _media_from_source(local_kit, product.get("image_urls"))
            if media:
                local_kit.patch_variant(variant_id, {"media": media})
                uploaded = len(media)
                full = local_kit.get_variant(variant_id)

        public_urls = _public_media_urls(local_kit, full)
        summary_updated = False
        if public_urls:
            desired_summary = _summary(public_urls)
            if desired_summary.strip() != _s(product.get("summary")):
                local_wa.call(
                    "shop.product.update",
                    http_method="POST",
                    params={"id": str(product["product_id"])},
                    data={"summary": desired_summary},
                )
                summary_updated = True
        return uploaded, summary_updated

    if use_parallel and media_queue:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            future_map = {pool.submit(media_one, row): row for row in media_queue}
            done = 0
            for future in concurrent.futures.as_completed(future_map):
                row = future_map[future]
                product = row["product"]
                done += 1
                try:
                    uploaded, summary_updated = future.result()
                    report["images_uploaded"] += uploaded
                    if summary_updated:
                        report["image_summary_updated"] += 1
                        report["webasyst_updated"] += 1
                except Exception as exc:
                    report["errors"].append({
                        "product_id": product.get("product_id"),
                        "supplier_sku": product.get("supplier_sku"),
                        "sku": product.get("sku"),
                        "stage": "images",
                        "error": str(exc)[:1200],
                    })
                if done % 50 == 0 or done == len(media_queue):
                    print("KIT image sync: %d/%d products" % (done, len(media_queue)), flush=True)
    else:
        for row in media_queue:
            product = row["product"]
            try:
                uploaded, summary_updated = media_one(row)
                report["images_uploaded"] += uploaded
                if summary_updated:
                    report["image_summary_updated"] += 1
                    report["webasyst_updated"] += 1
            except Exception as exc:
                report["errors"].append({
                    "product_id": product.get("product_id"),
                    "supplier_sku": product.get("supplier_sku"),
                    "sku": product.get("sku"),
                    "stage": "images",
                    "error": str(exc)[:1200],
                })

    report["categories_created"] = max(0, len(kit_categories) - category_count_before)
    report["characteristics_created"] = max(0, len(kit_characteristics) - characteristic_count_before)
    if report["errors"]:
        report["status"] = "partial_failure"
    return report

