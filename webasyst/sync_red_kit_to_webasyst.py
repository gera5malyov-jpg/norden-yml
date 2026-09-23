#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
KIT_API = "https://api.kit.yandex.net"
SKU_PREFIX = "RED-"
REPORT = HERE / "last_red_webasyst_sync.json"
DRY_RUN = str(os.getenv("DRY_RUN", "1")).strip().lower() not in {"0", "false", "no", "off"}
KIT_DELAY = float(os.getenv("RED_KIT_REQUEST_DELAY", "0.40"))
WA_DELAY = float(os.getenv("RED_WEBASYST_WRITE_DELAY", "0.55"))
MAX_PRODUCTS = int(os.getenv("RED_MAX_PRODUCTS", "0") or "0")

import sys
sys.path.insert(0, str(HERE))
from client import WebasystClient  # noqa: E402


def s(v):
    return str(v or "").strip()


def norm(v):
    return re.sub(
        r"[^0-9a-zа-яё]+",
        "",
        unicodedata.normalize("NFKC", s(v)).casefold(),
    )


def sku_key(v):
    return re.sub(r"\s+", "", s(v)).casefold()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


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


class KitClient:
    def __init__(self, token):
        self.token = s(token)
        if not self.token:
            raise RuntimeError("YANDEX_KIT_TOKEN is not set")
        self.session = requests.Session()
        self.last_request_at = 0.0

    def request(self, method, path, *, params=None, body=None, timeout=90):
        url = path if path.startswith("http") else KIT_API + path
        for attempt in range(15):
            delay = KIT_DELAY - (time.monotonic() - self.last_request_at)
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
                    headers=headers,
                    timeout=timeout,
                )
            except requests.RequestException:
                if attempt == 14:
                    raise
                time.sleep(min(15, attempt + 1))
                continue

            if response.status_code == 429:
                wait = response.headers.get("Retry-After")
                try:
                    pause = float(wait) if wait else min(20, 2 + attempt)
                except ValueError:
                    pause = min(20, 2 + attempt)
                time.sleep(pause)
                continue
            if response.status_code >= 500:
                if attempt == 14:
                    response.raise_for_status()
                time.sleep(min(15, attempt + 1))
                continue

            response.raise_for_status()
            return response.json() if response.content else {}

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


def char_value(entry):
    value = s(entry.get("value"))
    if value:
        return value
    vals = []
    for x in entry.get("values") or []:
        value = s(x)
        if value and value not in vals:
            vals.append(value)
    return " | ".join(vals)


def extimg_summary(urls):
    urls = [s(x) for x in urls if s(x)]
    if not urls:
        return ""
    return "[extimg]\n" + "\n".join(urls) + "\n[/extimg]"


def product_skus(product):
    skus = product.get("skus")
    if isinstance(skus, dict):
        return [x for x in skus.values() if isinstance(x, dict)]
    if isinstance(skus, list):
        return [x for x in skus if isinstance(x, dict)]
    return []


def load_wa_products(wa):
    out = []
    offset = 0
    while True:
        payload = wa.call(
            "shop.product.search",
            params={
                "hash": "search/query=RED-",
                "offset": offset,
                "limit": 1000,
                "fields": "id,name,type_id,summary,skus",
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


def feature_code(title):
    return "redkit_" + hashlib.sha1(norm(title).encode("utf-8")).hexdigest()[:16]


def is_scalar_feature(row):
    ftype = s(row.get("type")).lower()
    selectable_raw = row.get("selectable")
    try:
        selectable = bool(int(selectable_raw or 0))
    except Exception:
        selectable = bool(selectable_raw)
    return (
        bool(s(row.get("code")))
        and not selectable
        and (not ftype or any(x in ftype for x in ("varchar", "text", "double", "float", "int", "decimal")))
    )


def main():
    # One-shot de-duplication for the queued optimized run created while the
    # previous safe RED sync was already executing. If that previous run
    # committed a complete report, this specific queued run reuses it instead
    # of writing the same content twice. If the previous run failed, normal
    # processing continues.
    if os.getenv("GITHUB_RUN_ID") == "35918687571" and REPORT.exists():
        try:
            previous = json.loads(REPORT.read_text(encoding="utf-8"))
        except Exception:
            previous = {}
        if previous.get("complete") is True and previous.get("dry_run") is False:
            print(json.dumps(previous, ensure_ascii=False, indent=2), flush=True)
            return 0

    kit = KitClient(os.getenv("YANDEX_KIT_TOKEN", ""))
    wa = WebasystClient(min_request_interval=WA_DELAY)

    report = {
        "started_at": now_iso(),
        "dry_run": DRY_RUN,
        "source": "Yandex KIT",
        "target": "Webasyst",
        "sku_prefix": SKU_PREFIX,
        "policy": {
            "update_existing_only": True,
            "prices_touched": False,
            "stocks_touched": False,
            "sku_update_calls": 0,
            "images_destination": "Краткое описание [extimg]",
            "characteristics_source": "Yandex KIT",
        },
        "kit_variants_scanned": 0,
        "kit_red_variants": 0,
        "kit_unique_skus": 0,
        "kit_duplicate_skus": 0,
        "webasyst_products_scanned": 0,
        "webasyst_skus_scanned": 0,
        "webasyst_red_skus": 0,
        "webasyst_duplicate_red_skus": 0,
        "matched": 0,
        "missing_in_webasyst": 0,
        "product_conflicts": 0,
        "processed": 0,
        "updated": 0,
        "summaries_updated": 0,
        "variants_without_images": 0,
        "file_urls_resolved": 0,
        "features_created": 0,
        "features_planned_to_create": 0,
        "feature_values_written": 0,
        "status_values_written": 0,
        "descriptions_written": 0,
        "names_written": 0,
        "errors": [],
        "missing_skus_sample": [],
        "duplicate_kit_skus_sample": [],
        "duplicate_webasyst_skus_sample": [],
        "sample_updates": [],
        "complete": False,
    }

    kit_chars = kit.list_all("/v1/characteristics", {"status": "ACTIVE"}, "characteristics")
    char_by_id = {s(x.get("id")): x for x in kit_chars if s(x.get("id"))}

    variants = kit.list_all("/v1/variants", {"name": SKU_PREFIX}, "variants")
    report["kit_variants_scanned"] = len(variants)

    kit_by_sku = defaultdict(list)
    for variant in variants:
        sku = s(variant.get("sku"))
        if not sku.upper().startswith(SKU_PREFIX):
            continue
        report["kit_red_variants"] += 1
        kit_by_sku[sku_key(sku)].append((sku, variant))

    dup_kit = {k: rows for k, rows in kit_by_sku.items() if len(rows) > 1}
    report["kit_duplicate_skus"] = len(dup_kit)
    report["duplicate_kit_skus_sample"] = [rows[0][0] for rows in list(dup_kit.values())[:50]]
    kit_unique = {rows[0][0]: rows[0][1] for rows in kit_by_sku.values() if len(rows) == 1}
    report["kit_unique_skus"] = len(kit_unique)

    if MAX_PRODUCTS:
        kit_unique = dict(list(sorted(kit_unique.items()))[:MAX_PRODUCTS])
        report["max_products_limit"] = MAX_PRODUCTS

    products = load_wa_products(wa)
    report["webasyst_products_scanned"] = len(products)
    wa_by_sku = defaultdict(list)
    red_product_skus = defaultdict(list)
    for product in products:
        product_id = s(product.get("id"))
        for sku_row in product_skus(product):
            report["webasyst_skus_scanned"] += 1
            sku = s(sku_row.get("sku"))
            if not sku:
                continue
            if sku.upper().startswith(SKU_PREFIX):
                report["webasyst_red_skus"] += 1
                red_product_skus[product_id].append(sku)
            wa_by_sku[sku_key(sku)].append((product, sku_row))

    dup_wa = {
        key: rows for key, rows in wa_by_sku.items()
        if key.startswith(SKU_PREFIX.casefold()) and len(rows) > 1
    }
    report["webasyst_duplicate_red_skus"] = len(dup_wa)
    report["duplicate_webasyst_skus_sample"] = [
        s(rows[0][1].get("sku")) for rows in list(dup_wa.values())[:50]
    ]

    conflicting_product_ids = {pid for pid, skus in red_product_skus.items() if len(skus) > 1}
    report["product_conflicts"] = len(conflicting_product_ids)

    all_features = listify(wa.call("shop.feature.getList"), ("features", "items"))
    global_by_title = defaultdict(list)
    for row in all_features:
        global_by_title[norm(row.get("name") or row.get("title"))].append(row)

    type_feature_cache = {}
    selected_feature_cache = {}
    created_codes = set()

    def type_features(type_id):
        type_id = s(type_id)
        if type_id not in type_feature_cache:
            rows = listify(
                wa.call("shop.feature.getList", params={"type_id": type_id}),
                ("features", "items"),
            )
            by_title = defaultdict(list)
            for row in rows:
                by_title[norm(row.get("name") or row.get("title"))].append(row)
            type_feature_cache[type_id] = by_title
        return type_feature_cache[type_id]

    def pick_feature(title, type_id):
        cache_key = (s(type_id), norm(title))
        if cache_key in selected_feature_cache:
            return selected_feature_cache[cache_key]

        candidates = []
        candidates.extend(type_features(type_id).get(norm(title), []))
        candidates.extend(global_by_title.get(norm(title), []))
        dedup = {}
        for row in candidates:
            code = s(row.get("code"))
            if code:
                dedup[code] = row
        usable = [row for row in dedup.values() if is_scalar_feature(row)]

        if len(usable) == 1:
            code = s(usable[0].get("code"))
            selected_feature_cache[cache_key] = code
            return code

        typed_codes = {
            s(row.get("code"))
            for row in type_features(type_id).get(norm(title), [])
            if is_scalar_feature(row)
        }
        if len(typed_codes) == 1:
            code = next(iter(typed_codes))
            selected_feature_cache[cache_key] = code
            return code

        code = feature_code(title)
        if DRY_RUN:
            if code not in created_codes:
                created_codes.add(code)
                report["features_planned_to_create"] += 1
            selected_feature_cache[cache_key] = code
            return code

        existing_code = next((x for x in all_features if s(x.get("code")) == code), None)
        if existing_code is None:
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
                row = {"code": code, "name": title, "type": "varchar", "selectable": 0}
            all_features.append(row)
            global_by_title[norm(title)].append(row)
            report["features_created"] += 1

        selected_feature_cache[cache_key] = code
        return code

    file_url_cache = {}

    def image_urls(variant):
        result = []
        media = sorted(
            [
                x for x in (variant.get("media") or [])
                if isinstance(x, dict) and s(x.get("type")).upper() == "IMAGE"
            ],
            key=lambda x: int(x.get("display_sequence") or 0),
        )
        if DRY_RUN:
            # Preflight validates matching/content without spending API calls
            # resolving every KIT image URL. Live mode resolves and writes all URLs.
            return [f"kit-file:{s(row.get('image_id'))}" for row in media if s(row.get("image_id"))]
        for row in media:
            file_id = s(row.get("image_id"))
            if not file_id:
                continue
            if file_id not in file_url_cache:
                payload = kit.request("GET", f"/v1/files/{file_id}")
                file_url_cache[file_id] = s(payload.get("url"))
                if file_url_cache[file_id]:
                    report["file_urls_resolved"] += 1
            url = file_url_cache.get(file_id)
            if url and url not in result:
                result.append(url)
        return result

    def source_characteristics(variant):
        out = {}
        for entry in variant.get("characteristics") or []:
            cid = s(entry.get("characteristic_id"))
            title = s((char_by_id.get(cid) or {}).get("title"))
            value = char_value(entry)
            if title and value:
                out[title] = value

        brand = s(variant.get("brand"))
        barcode = s(variant.get("barcode"))
        vat = variant.get("vat")
        seo_h1 = s(variant.get("seo_h1"))
        seo_title = s(variant.get("seo_title"))
        seo_description = s(variant.get("seo_description"))

        if brand:
            out["Бренд"] = brand
        if barcode:
            out["Штрихкод"] = barcode
        if vat not in (None, ""):
            out["НДС"] = s(vat)
        if seo_h1:
            out["SEO H1"] = seo_h1
        if seo_title:
            out["SEO title"] = seo_title
        if seo_description:
            out["SEO description"] = seo_description
        return out

    for sku, variant in sorted(kit_unique.items(), key=lambda x: sku_key(x[0])):
        try:
            key = sku_key(sku)

            matches = wa_by_sku.get(key, [])
            if not matches:
                report["missing_in_webasyst"] += 1
                if len(report["missing_skus_sample"]) < 100:
                    report["missing_skus_sample"].append(sku)
                continue
            if len(matches) > 1:
                raise RuntimeError(f"Duplicate Webasyst SKU: {sku}")

            product, sku_row = matches[0]
            product_id = s(product.get("id"))
            type_id = s(product.get("type_id"))
            if not product_id:
                raise RuntimeError("Webasyst product has no id")
            if product_id in conflicting_product_ids:
                raise RuntimeError(
                    f"Webasyst product {product_id} contains multiple RED SKUs: "
                    + ", ".join(red_product_skus[product_id])
                )

            report["matched"] += 1

            features = {}
            source = source_characteristics(variant)
            for title, value in source.items():
                code = pick_feature(title, type_id)
                if code:
                    features[code] = value

            urls = image_urls(variant)
            desired_summary = extimg_summary(urls) if urls else None
            if not urls:
                report["variants_without_images"] += 1

            data = {"features": features}
            name = s(variant.get("name"))
            description = s(variant.get("description"))
            status = s(variant.get("status")).upper()

            if name:
                data["name"] = name
                report["names_written"] += 1
            if description:
                data["description"] = description
                report["descriptions_written"] += 1
            if desired_summary is not None:
                data["summary"] = desired_summary
                if s(product.get("summary")) != desired_summary:
                    report["summaries_updated"] += 1
            if status in {"PUBLISHED", "ACTIVE"}:
                data["status"] = 1
                report["status_values_written"] += 1
            elif status in {"ARCHIVED", "DRAFT", "HIDDEN", "INACTIVE"}:
                data["status"] = 0
                report["status_values_written"] += 1

            if not DRY_RUN:
                wa.call(
                    "shop.product.update",
                    http_method="POST",
                    params={"id": product_id},
                    data=data,
                )

            report["updated"] += 1
            report["processed"] += 1
            report["feature_values_written"] += len(features)

            if len(report["sample_updates"]) < 30:
                report["sample_updates"].append({
                    "sku": sku,
                    "product_id": product_id,
                    "type_id": type_id,
                    "name": name,
                    "description_written": bool(description),
                    "images_in_summary": len(urls),
                    "features": len(features),
                    "status_from_kit": status,
                    "price_stock_untouched": True,
                })

        except Exception as exc:
            report["errors"].append({
                "sku": sku,
                "variant_id": s(variant.get("id")),
                "message": str(exc)[:1200],
            })

    report["finished_at"] = now_iso()
    critical_duplicates = (
        report["kit_duplicate_skus"]
        + report["webasyst_duplicate_red_skus"]
        + report["product_conflicts"]
    )
    report["complete"] = (
        not report["errors"]
        and critical_duplicates == 0
        and report["matched"] == report["updated"]
        and report["policy"]["sku_update_calls"] == 0
        and report["policy"]["prices_touched"] is False
        and report["policy"]["stocks_touched"] is False
    )
    report["status"] = "УСПЕШНО" if report["complete"] else "ЗАВЕРШЕНО С ОШИБКАМИ"

    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
