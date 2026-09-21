#!/usr/bin/env python3
import json
import os
import re
import sys
import time
from collections import defaultdict

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from client import WebasystClient, WebasystAPIError

KIT_BASE = "https://api.kit.yandex.net"
TARGET_TYPE_NAME = "33 Кровати-333"
TARGET_WA_STOCK = "33кровати"
TARGET_KIT_STOCK = "СПБ"
TARGET_BRAND = "4 Сезона"
ARTICLE_TITLE = "Артикул"


def s(v):
    return str(v or "").strip()


def norm(v):
    return re.sub(r"[^0-9a-zа-яё]+", "", s(v).casefold())


class Kit:
    def __init__(self, token):
        token = s(token)
        if not token:
            raise RuntimeError("YANDEX_KIT_TOKEN is not configured")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})
        self.last = 0.0

    def request(self, path, params=None):
        delay = 0.42 - (time.monotonic() - self.last)
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()
        r = self.session.get(KIT_BASE + path, params=params, timeout=120)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def items(payload):
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        for key in ("items", "results", "variants", "warehouses", "characteristics", "products"):
            if isinstance(payload.get(key), list):
                return payload[key]
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
        for key in ("total", "total_count"):
            if isinstance(payload.get(key), int):
                return payload[key]
        meta = payload.get("meta")
        if isinstance(meta, dict):
            for key in ("total", "total_count"):
                if isinstance(meta.get(key), int):
                    return meta[key]
        return None

    def all(self, path, params=None):
        out = []
        page = 1
        while True:
            q = dict(params or {})
            q.update({"page": page, "per_page": 100})
            payload = self.request(path, q)
            rows = [x for x in self.items(payload) if isinstance(x, dict)]
            out.extend(rows)
            total = self.total(payload)
            if not rows or (total is not None and len(out) >= total) or (total is None and len(rows) < 100):
                return out
            page += 1


def row_name(row):
    return s(row.get("name") or row.get("title"))


def find_exact(rows, wanted):
    return [r for r in rows if norm(row_name(r)) == norm(wanted)]


def char_value(v, cid):
    for x in v.get("characteristics") or []:
        if s(x.get("characteristic_id")) == s(cid):
            vals = x.get("values") or []
            return s(x.get("value")) or (s(vals[0]) if vals else "")
    return ""


def main():
    wa = WebasystClient()
    kit = Kit(os.getenv("YANDEX_KIT_TOKEN", ""))

    types = wa.call("shop.type.getList")
    type_rows = list(types.values()) if isinstance(types, dict) else list(types or [])
    type_matches = find_exact(type_rows, TARGET_TYPE_NAME)
    if len(type_matches) != 1:
        raise RuntimeError(f"Expected exactly one Webasyst type {TARGET_TYPE_NAME!r}; found {len(type_matches)}")
    target_type = type_matches[0]
    type_id = s(target_type.get("id"))

    stocks = wa.call("shop.stock.getList")
    stock_rows = list(stocks.values()) if isinstance(stocks, dict) else list(stocks or [])
    stock_matches = find_exact(stock_rows, TARGET_WA_STOCK)
    if len(stock_matches) != 1:
        raise RuntimeError(f"Expected exactly one Webasyst stock {TARGET_WA_STOCK!r}; found {len(stock_matches)}")
    wa_stock = stock_matches[0]

    # Verify current target type count and SKU availability.
    offset = 0
    products = []
    while True:
        payload = wa.call(
            "shop.product.search",
            params={"hash": f"type/{type_id}", "offset": offset, "limit": 1000, "fields": "*,skus"},
        )
        if isinstance(payload, dict):
            batch = payload.get("products") or payload.get("items") or []
            total = payload.get("count") or payload.get("total_count")
        else:
            batch = payload or []
            total = None
        batch = [x for x in batch if isinstance(x, dict)]
        products.extend(batch)
        if not batch or len(batch) < 1000 or (total is not None and len(products) >= int(total)):
            break
        offset += len(batch)

    kit_wh = kit.all("/v1/warehouses", {"status": "ACTIVE"})
    kit_wh_matches = [x for x in kit_wh if row_name(x) == TARGET_KIT_STOCK]
    if len(kit_wh_matches) != 1:
        raise RuntimeError(f"Expected exactly one KIT stock {TARGET_KIT_STOCK!r}; found {len(kit_wh_matches)}")
    kit_spb = kit_wh_matches[0]

    chars = kit.all("/v1/characteristics", {"status": ["ACTIVE"]})
    article_matches = [x for x in chars if norm(row_name(x)) == norm(ARTICLE_TITLE)]
    if len(article_matches) != 1:
        raise RuntimeError(f"Expected exactly one KIT characteristic {ARTICLE_TITLE!r}; found {len(article_matches)}")
    article_id = s(article_matches[0].get("id"))

    # Scan KIT only to count brand 4 Сезона and inspect the fields needed by sync.
    variants = kit.all("/v1/variants")
    managed = [v for v in variants if s(v.get("brand")) == TARGET_BRAND]
    with_article = [v for v in managed if char_value(v, article_id)]

    sample = managed[0] if managed else {}
    safe_sample = {
        "keys": sorted(sample.keys()),
        "pricing_keys": sorted((sample.get("pricing") or {}).keys()),
        "stock_keys": sorted((sample.get("stocks") or [{}])[0].keys()) if sample.get("stocks") else [],
        "media_keys": sorted((sample.get("media") or [{}])[0].keys()) if sample.get("media") else [],
        "characteristic_entry_keys": sorted((sample.get("characteristics") or [{}])[0].keys()) if sample.get("characteristics") else [],
        "has_description": bool(s(sample.get("description"))),
        "media_count": len(sample.get("media") or []),
    }

    wa_skus = []
    for p in products:
        skus = p.get("skus")
        if isinstance(skus, dict):
            wa_skus.extend(x for x in skus.values() if isinstance(x, dict))
        elif isinstance(skus, list):
            wa_skus.extend(x for x in skus if isinstance(x, dict))

    result = {
        "webasyst_type": {"id": type_id, "name": row_name(target_type), "product_count": len(products), "sku_count": len(wa_skus)},
        "webasyst_stock": {"id": s(wa_stock.get("id")), "name": row_name(wa_stock)},
        "kit_stock": {"id": s(kit_spb.get("id")), "name": row_name(kit_spb)},
        "kit_brand_variants": len(managed),
        "kit_brand_variants_with_article": len(with_article),
        "kit_article_characteristic_id": article_id,
        "kit_variant_shape": safe_sample,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
