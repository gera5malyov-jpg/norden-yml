#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webasyst"))

from client import WebasystClient

BRAND = "Б2Б Фабрика"
TYPE_NAME = "Мебельная фабрика В2В-335"
ROUTE_TITLE = "Webasyst"
ROUTE_VALUE = "335"
SKU_PREFIX = "335-"
PURCHASE_TITLE = "Закупочная цена"


def s(v):
    return str(v or "").strip()


def norm(v):
    return re.sub(r"[^0-9a-zа-яё]+", "", unicodedata.normalize("NFKC", s(v)).casefold())


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
    matches = [x for x in rows if norm(x.get("name") or x.get("title")) == norm(wanted)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one {label} {wanted!r}; found {len(matches)}")
    return matches[0]


def load_kit_module():
    path = ROOT / "kenner-kit" / "sync_kenner_kit.py"
    spec = importlib.util.spec_from_file_location("kit_cleanup_client", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def char_value(variant, cid):
    for row in variant.get("characteristics") or []:
        if s(row.get("characteristic_id")) != s(cid):
            continue
        values = row.get("values") or []
        return s(row.get("value")) or (s(values[0]) if values else "")
    return ""


def main():
    if not s(os.getenv("WEBASYST_API_TOKEN")):
        raise RuntimeError("WEBASYST_API_TOKEN missing")
    if not s(os.getenv("YANDEX_KIT_TOKEN")):
        raise RuntimeError("YANDEX_KIT_TOKEN missing")

    report = {
        "brand": BRAND,
        "purchase_characteristic": PURCHASE_TITLE,
        "webasyst": {
            "managed_products": 0,
            "products_cleared": 0,
            "feature_definitions_found": [],
            "feature_definitions_deleted": [],
            "feature_definitions_left": [],
        },
        "kit": {
            "purchase_characteristics_found": [],
            "managed_variants": 0,
            "variants_cleared": 0,
            "non_b2b_references": 0,
            "characteristics_deleted_or_archived": [],
            "characteristics_left": [],
        },
        "errors": [],
    }

    # ---- Webasyst: clear the value only from the B2B-335 population. ----
    wa = WebasystClient(min_request_interval=0.55)
    types = listify(wa.call("shop.type.getList"))
    type_row = exact_one(types, TYPE_NAME, "Webasyst type")
    type_id = s(type_row.get("id"))

    features = listify(wa.call("shop.feature.getList"), ("features", "items"))
    route_matches = [
        x for x in features
        if norm(x.get("name") or x.get("title")) == norm(ROUTE_TITLE) and s(x.get("code"))
    ]
    route_by_code = {s(x.get("code")): x for x in route_matches}
    if len(route_by_code) != 1:
        raise RuntimeError(f"Expected one Webasyst routing feature; found {len(route_by_code)}")
    route_code = next(iter(route_by_code))

    purchase_features = [
        x for x in features
        if norm(x.get("name") or x.get("title")) == norm(PURCHASE_TITLE) and s(x.get("code"))
    ]
    report["webasyst"]["feature_definitions_found"] = [
        {"id": s(x.get("id")), "code": s(x.get("code"))} for x in purchase_features
    ]
    purchase_codes = [s(x.get("code")) for x in purchase_features]

    offset = 0
    products = []
    while True:
        payload = wa.call(
            "shop.product.search",
            params={
                "hash": f"type/{type_id}",
                "offset": offset,
                "limit": 1000,
                "fields": "*,skus",
            },
        )
        batch = listify(payload, ("products", "items"))
        products.extend(batch)
        if not batch or len(batch) < 1000:
            break
        offset += len(batch)

    for product in products:
        pid = s(product.get("id"))
        if not pid:
            continue
        info = wa.call("shop.product.getInfo", params={"id": pid})
        values = (info or {}).get("features") or {}
        if s(values.get(route_code)) != ROUTE_VALUE:
            continue
        report["webasyst"]["managed_products"] += 1

        to_clear = {code: "" for code in purchase_codes if code in values}
        if not to_clear:
            continue
        wa.call(
            "shop.product.update",
            http_method="POST",
            params={"id": pid},
            data={"features": to_clear},
        )
        report["webasyst"]["products_cleared"] += 1

    # Delete only integration-specific definitions created by this B2B sync.
    # A similarly named shared feature with another code is intentionally not deleted globally.
    for feature in purchase_features:
        code = s(feature.get("code"))
        if code.startswith("b2b335_"):
            try:
                wa.call(
                    "shop.feature.delete",
                    http_method="POST",
                    data={"code": code},
                )
                report["webasyst"]["feature_definitions_deleted"].append(code)
            except Exception as exc:
                report["errors"].append({"system": "webasyst", "stage": "delete_feature", "code": code, "error": str(exc)})
        else:
            report["webasyst"]["feature_definitions_left"].append(code)

    # ---- KIT: remove characteristic from B2B variants. ----
    kit_mod = load_kit_module()
    kit = kit_mod.KitClient(s(os.getenv("YANDEX_KIT_TOKEN")))

    active_chars = kit.list_all("/v1/characteristics", {"status": "ACTIVE"}, "characteristics")
    purchase_chars = [x for x in active_chars if norm(x.get("title")) == norm(PURCHASE_TITLE)]
    report["kit"]["purchase_characteristics_found"] = [
        {"id": s(x.get("id")), "title": s(x.get("title"))} for x in purchase_chars
    ]
    purchase_ids = {s(x.get("id")) for x in purchase_chars if s(x.get("id"))}

    route_chars = [x for x in active_chars if norm(x.get("title")) == norm(ROUTE_TITLE)]
    route_id = s(route_chars[0].get("id")) if route_chars else ""

    # Scan all variants once so global characteristic deletion is safe.
    all_variants = kit.list_all("/v1/variants", {}, "variants")
    non_b2b_refs = set()

    for row in all_variants:
        vid = s(row.get("id"))
        if not vid:
            continue
        characteristics = list(row.get("characteristics") or [])
        present = {s(x.get("characteristic_id")) for x in characteristics}
        target_ids = present & purchase_ids
        if not target_ids:
            continue

        is_b2b = (
            norm(row.get("brand")) == norm(BRAND)
            or s(row.get("sku")).startswith(SKU_PREFIX)
            or (route_id and char_value(row, route_id) == ROUTE_VALUE)
        )

        if not is_b2b:
            non_b2b_refs.update(target_ids)
            continue

        report["kit"]["managed_variants"] += 1
        filtered = [
            x for x in characteristics
            if s(x.get("characteristic_id")) not in purchase_ids
        ]
        if len(filtered) != len(characteristics):
            try:
                kit.patch_variant(vid, {"characteristics": filtered})
                report["kit"]["variants_cleared"] += 1
            except Exception as exc:
                report["errors"].append({"system": "kit", "stage": "clear_variant", "variant_id": vid, "error": str(exc)})

    report["kit"]["non_b2b_references"] = len(non_b2b_refs)

    # KIT's UI deletion moves a characteristic to Archive. Do the equivalent only
    # when the characteristic is not referenced by non-B2B products.
    for cid in sorted(purchase_ids):
        if cid in non_b2b_refs:
            report["kit"]["characteristics_left"].append({
                "id": cid,
                "reason": "used_by_non_b2b_products",
            })
            continue

        removed = False
        try:
            kit.request("DELETE", f"/v1/characteristics/{cid}")
            report["kit"]["characteristics_deleted_or_archived"].append({"id": cid, "method": "DELETE"})
            removed = True
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", 0)
            if status not in (400, 404, 405, 409, 422):
                report["errors"].append({"system": "kit", "stage": "delete_characteristic", "id": cid, "error": str(exc)})
        except Exception as exc:
            report["errors"].append({"system": "kit", "stage": "delete_characteristic", "id": cid, "error": str(exc)})

        if not removed:
            try:
                kit.request("PATCH", f"/v1/characteristics/{cid}", body={"status": "ARCHIVED"})
                report["kit"]["characteristics_deleted_or_archived"].append({"id": cid, "method": "PATCH_ARCHIVED"})
                removed = True
            except Exception as exc:
                report["kit"]["characteristics_left"].append({
                    "id": cid,
                    "reason": "api_delete_archive_failed",
                    "error": str(exc)[:500],
                })

    report["status"] = "ok" if not report["errors"] else "degraded"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
