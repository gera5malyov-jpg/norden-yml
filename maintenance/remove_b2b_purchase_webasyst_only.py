#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webasyst"))

from client import WebasystClient

TYPE_NAME = "Мебельная фабрика В2В-335"
ROUTE_TITLE = "Webasyst"
ROUTE_VALUE = "335"
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
        raise RuntimeError(f"Expected one {label} {wanted!r}; found {len(matches)}")
    return matches[0]


def main():
    wa = WebasystClient(min_request_interval=0.55)

    report = {
        "type": TYPE_NAME,
        "route": f"{ROUTE_TITLE}={ROUTE_VALUE}",
        "target": PURCHASE_TITLE,
        "products_in_type": 0,
        "managed_products": 0,
        "products_with_purchase_feature": 0,
        "products_cleared": 0,
        "feature_definitions_found": [],
        "feature_definitions_deleted": [],
        "feature_definitions_left": [],
        "errors": [],
    }

    types = listify(wa.call("shop.type.getList"))
    type_id = s(exact_one(types, TYPE_NAME, "product type").get("id"))

    features = listify(wa.call("shop.feature.getList"), ("features", "items"))

    route_rows = [
        x for x in features
        if norm(x.get("name") or x.get("title")) == norm(ROUTE_TITLE) and s(x.get("code"))
    ]
    route_by_code = {s(x.get("code")): x for x in route_rows}
    if len(route_by_code) != 1:
        raise RuntimeError(f"Expected one routing feature Webasyst; found {len(route_by_code)}")
    route_code = next(iter(route_by_code))

    purchase_rows = [
        x for x in features
        if norm(x.get("name") or x.get("title")) == norm(PURCHASE_TITLE) and s(x.get("code"))
    ]
    purchase_codes = [s(x.get("code")) for x in purchase_rows]
    report["feature_definitions_found"] = [
        {"id": s(x.get("id")), "code": s(x.get("code"))} for x in purchase_rows
    ]

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

    report["products_in_type"] = len(products)

    for product in products:
        pid = s(product.get("id"))
        if not pid:
            continue
        info = wa.call("shop.product.getInfo", params={"id": pid})
        values = (info or {}).get("features") or {}
        if s(values.get(route_code)) != ROUTE_VALUE:
            continue

        report["managed_products"] += 1
        present_codes = [code for code in purchase_codes if code in values and s(values.get(code))]
        if not present_codes:
            continue

        report["products_with_purchase_feature"] += 1
        try:
            wa.call(
                "shop.product.update",
                http_method="POST",
                params={"id": pid},
                data={"features": {code: "" for code in present_codes}},
            )
            report["products_cleared"] += 1
        except Exception as exc:
            report["errors"].append({"product_id": pid, "stage": "clear_value", "error": str(exc)})

    # Characteristics created by this integration have b2b335_* codes and are safe to delete globally.
    for feature in purchase_rows:
        code = s(feature.get("code"))
        if code.startswith("b2b335_"):
            try:
                wa.call("shop.feature.delete", http_method="POST", data={"code": code})
                report["feature_definitions_deleted"].append(code)
            except Exception as exc:
                report["errors"].append({"code": code, "stage": "delete_feature", "error": str(exc)})
        else:
            report["feature_definitions_left"].append(code)

    report["status"] = "ok" if not report["errors"] else "degraded"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
