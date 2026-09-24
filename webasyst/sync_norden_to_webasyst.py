#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from client import WebasystClient

TYPE_NAME = "NORDEN-100"
WA_STOCK_NAME = "Основной склад"
KIT_STOCK_NAME = "МСК"
BRAND = "Norden"
REPORT = HERE / "last_norden_webasyst_sync.json"
WRITE_DELAY = float(os.getenv("NORDEN_WEBASYST_WRITE_DELAY", "0.60"))
MONEY = Decimal("0.01")


def s(v):
    return str(v or "").strip()


def norm(v):
    return re.sub(r"[^0-9a-zа-яё]+", "", unicodedata.normalize("NFKC", s(v)).casefold())


def money(v):
    if v in (None, ""):
        return None
    try:
        d = Decimal(str(v).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if d < 0:
        return None
    return d.quantize(MONEY, rounding=ROUND_HALF_UP)


def money_str(v):
    d = money(v)
    return None if d is None else f"{d:.2f}"


def as_int(v):
    try:
        return max(0, int(Decimal(str(v or 0))))
    except Exception:
        return 0


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


def product_skus(product):
    rows = product.get("skus")
    if isinstance(rows, dict):
        return [x for x in rows.values() if isinstance(x, dict)]
    if isinstance(rows, list):
        return [x for x in rows if isinstance(x, dict)]
    return []


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
        total = payload.get("count") or payload.get("total_count") if isinstance(payload, dict) else None
        out.extend(batch)
        if not batch or len(batch) < 1000:
            break
        if total not in (None, "") and len(out) >= int(total):
            break
        offset += len(batch)
    return out


def wa_stock_qty(sku, stock_id):
    stock = sku.get("stock")
    if isinstance(stock, dict):
        if stock_id in stock:
            return as_int(stock.get(stock_id))
        if str(stock_id) in stock:
            return as_int(stock.get(str(stock_id)))
    if isinstance(stock, list):
        for row in stock:
            if isinstance(row, dict) and s(row.get("stock_id") or row.get("id")) == s(stock_id):
                return as_int(row.get("count") if "count" in row else row.get("quantity"))
    return None


def load_norden_module():
    path = ROOT / "norden-kit" / "sync_norden_kit.py"
    spec = importlib.util.spec_from_file_location("norden_kit_sync_module", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def char_value(variant, cid):
    for row in variant.get("characteristics") or []:
        if s(row.get("characteristic_id")) != s(cid):
            continue
        vals = row.get("values") or []
        return s(row.get("value")) or (s(vals[0]) if vals else "")
    return ""


def kit_stock_qty(variant, warehouse_id):
    for row in variant.get("stocks") or []:
        if s(row.get("warehouse_id")) == s(warehouse_id):
            return as_int(row.get("quantity"))
    return 0


def feature_value(v):
    if v in (None, ""):
        return ""
    if isinstance(v, dict):
        for key in ("value", "name", "title"):
            if s(v.get(key)):
                return s(v.get(key))
        vals = [feature_value(x) for x in v.values()]
        return " | ".join(x for x in vals if x)
    if isinstance(v, (list, tuple)):
        vals = [feature_value(x) for x in v]
        return " | ".join(dict.fromkeys(x for x in vals if x))
    return s(v)


def main():
    started = now_iso()
    mod = load_norden_module()
    kit = mod.KitClient(os.environ.get("YANDEX_KIT_TOKEN", ""))
    wa = WebasystClient(min_request_interval=WRITE_DELAY)

    report = {
        "started_at": started,
        "type_name": TYPE_NAME,
        "type_id": None,
        "webasyst_stock": WA_STOCK_NAME,
        "webasyst_stock_id": None,
        "kit_stock": KIT_STOCK_NAME,
        "kit_stock_id": None,
        "source": None,
        "webasyst_products": 0,
        "webasyst_skus": 0,
        "matched_exact_sku": 0,
        "matched_by_code": 0,
        "missing_in_kit": 0,
        "missing_source_price": 0,
        "updated": 0,
        "unchanged": 0,
        "stock_zeroed_for_missing_kit": 0,
        "errors": [],
        "warnings": [],
        "sample_updates": [],
        "sample_missing": [],
        "complete": False,
    }

    types = listify(wa.call("shop.type.getList"))
    type_matches = [x for x in types if norm(x.get("name") or x.get("title")) == norm(TYPE_NAME)]
    if len(type_matches) != 1:
        raise RuntimeError(f"Expected exactly one Webasyst type {TYPE_NAME!r}, found {len(type_matches)}")
    type_id = s(type_matches[0].get("id"))
    report["type_id"] = type_id

    stocks = listify(wa.call("shop.stock.getList"))
    stock_matches = [x for x in stocks if norm(x.get("name") or x.get("title")) == norm(WA_STOCK_NAME)]
    if len(stock_matches) != 1:
        raise RuntimeError(f"Expected exactly one Webasyst stock {WA_STOCK_NAME!r}, found {len(stock_matches)}")
    wa_stock_id = s(stock_matches[0].get("id"))
    report["webasyst_stock_id"] = wa_stock_id

    kit_warehouses = kit.warehouses()
    kit_stock_matches = [
        x for x in kit_warehouses
        if s(x.get("title")) == KIT_STOCK_NAME and s(x.get("id"))
    ]
    if len(kit_stock_matches) != 1:
        raise RuntimeError(f"Expected exactly one KIT warehouse {KIT_STOCK_NAME!r}, found {len(kit_stock_matches)}")
    kit_stock_id = s(kit_stock_matches[0].get("id"))
    report["kit_stock_id"] = kit_stock_id

    _, _, code_site_id, _ = mod.resolve_special_characteristics(kit)

    secret = os.environ.get("NORDEN_SECRET", "").strip()
    source, _, source_kind, api_error = mod.load_source(secret, short=True)
    report["source"] = source_kind
    report["api_error"] = api_error
    if len(source) < 100:
        raise RuntimeError(f"Safety stop: unexpectedly small Norden price source ({len(source)})")
    source_articles = list(source)

    mapping = mod.load_mapping()

    feature_defs = listify(wa.call("shop.feature.getList"), ("features", "items"))
    feature_title_by_code = {
        s(x.get("code")): s(x.get("name") or x.get("title"))
        for x in feature_defs if s(x.get("code"))
    }
    code_feature_codes = {
        code for code, title in feature_title_by_code.items()
        if norm(title) in {norm("Код для сайта"), norm("Код Norden")}
    }

    products = load_wa_products(wa, type_id)
    report["webasyst_products"] = len(products)

    wa_entries = []
    for product in products:
        for sku_row in product_skus(product):
            sku = s(sku_row.get("sku"))
            if sku:
                wa_entries.append((product, sku_row, sku))
    report["webasyst_skus"] = len(wa_entries)

    def exact_kit_by_sku(sku):
        payload = kit.request("GET", "/v1/variants", params={"name": sku, "page": 1, "per_page": 100})
        rows = [
            x for x in kit.items(payload)
            if s(x.get("sku")) == sku
            and s(x.get("brand")).casefold() == BRAND.casefold()
            and s(x.get("status")).upper() != "ARCHIVED"
        ]
        if len(rows) == 1:
            return kit.get_variant(s(rows[0].get("id")))
        if len(rows) > 1:
            raise RuntimeError(f"duplicate active KIT Norden SKU: {sku} ({len(rows)})")
        return None

    def wa_code_for_site(product_id):
        info = wa.call("shop.product.getInfo", params={"id": product_id})
        feats = info.get("features") or {} if isinstance(info, dict) else {}
        if isinstance(feats, dict):
            for code, value in feats.items():
                if s(code) in code_feature_codes:
                    v = feature_value(value)
                    if v:
                        return v
        return ""

    def mapped_kit_by_article(article):
        matched_article = mod.match_source_article(article, source_articles) or article
        rows = (mapping.get("variants") or {}).get(matched_article) or []
        active = []
        for row in rows:
            vid = s(row.get("variant_id"))
            if not vid:
                continue
            try:
                current = kit.get_variant(vid)
            except Exception:
                continue
            if s(current.get("brand")).casefold() != BRAND.casefold():
                continue
            if s(current.get("status")).upper() == "ARCHIVED":
                continue
            active.append(current)
        if len(active) == 1:
            return active[0]
        if len(active) > 1:
            raise RuntimeError(f"multiple active KIT Norden cards for source article {matched_article}")
        return None

    for product, sku_row, sku in wa_entries:
        try:
            variant = exact_kit_by_sku(sku)
            match_mode = "sku"
            wa_code = ""
            if variant is None:
                wa_code = wa_code_for_site(s(product.get("id")))
                if wa_code:
                    variant = mapped_kit_by_article(wa_code)
                    if variant is not None:
                        match_mode = "code"

            if variant is None:
                report["missing_in_kit"] += 1
                if len(report["sample_missing"]) < 100:
                    report["sample_missing"].append({
                        "sku": sku,
                        "product_id": s(product.get("id")),
                        "name": s(product.get("name")),
                        "code_for_site": wa_code,
                    })
                current_stock = wa_stock_qty(sku_row, wa_stock_id)
                if current_stock is None or current_stock != 0:
                    wa.call(
                        "shop.product.skus.update",
                        http_method="POST",
                        params={"id": s(sku_row.get("id"))},
                        data={"stock": {wa_stock_id: "0"}},
                    )
                    report["stock_zeroed_for_missing_kit"] += 1
                continue

            if match_mode == "sku":
                report["matched_exact_sku"] += 1
            else:
                report["matched_by_code"] += 1

            article = char_value(variant, code_site_id)
            source_article = mod.match_source_article(article, source_articles)
            item = source.get(source_article) if source_article else None

            stock = kit_stock_qty(variant, kit_stock_id)
            changes = {}

            current_stock = wa_stock_qty(sku_row, wa_stock_id)
            if current_stock is None or current_stock != stock:
                changes["stock"] = {wa_stock_id: str(stock)}

            if not item or item.get("purchase") is None or item.get("purchase") <= 0:
                report["missing_source_price"] += 1
                if len(report["warnings"]) < 200:
                    report["warnings"].append(
                        f"{sku}: нет положительной оптовой цены Norden для кода {article!r}; обновлён только остаток"
                    )
                desired_purchase = desired_sale = desired_compare = None
            else:
                desired_purchase = money(item["purchase"])
                desired_sale = money(Decimal(str(item["purchase"])) * Decimal("1.25"))
                desired_compare = money(Decimal(str(item["purchase"])) * Decimal("1.60"))

                if money(sku_row.get("purchase_price")) != desired_purchase:
                    changes["purchase_price"] = money_str(desired_purchase)
                if money(sku_row.get("price")) != desired_sale:
                    changes["price"] = money_str(desired_sale)
                if money(sku_row.get("compare_price")) != desired_compare:
                    changes["compare_price"] = money_str(desired_compare)

            if changes:
                wa.call(
                    "shop.product.skus.update",
                    http_method="POST",
                    params={"id": s(sku_row.get("id"))},
                    data=changes,
                )
                report["updated"] += 1
                if len(report["sample_updates"]) < 100:
                    report["sample_updates"].append({
                        "sku": sku,
                        "match": match_mode,
                        "kit_sku": s(variant.get("sku")),
                        "code_for_site": article,
                        "fields": sorted(changes),
                        "purchase_price": money_str(desired_purchase) if desired_purchase is not None else None,
                        "price": money_str(desired_sale) if desired_sale is not None else None,
                        "compare_price": money_str(desired_compare) if desired_compare is not None else None,
                        "stock": stock,
                    })
            else:
                report["unchanged"] += 1

        except Exception as exc:
            report["errors"].append({
                "sku": sku,
                "product_id": s(product.get("id")),
                "message": str(exc)[:1000],
            })

    report["finished_at"] = now_iso()
    report["complete"] = not report["errors"]
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
