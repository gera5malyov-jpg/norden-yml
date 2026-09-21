#!/usr/bin/env python3
from __future__ import annotations

import hashlib
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

TYPE_NAME = "Мебельная фабрика В2В-335"
EXPECTED_TYPE_ID = ""
WA_STOCK_NAME = "Основной склад"
EXPECTED_WA_STOCK_ID = "66"
KIT_STOCK_NAME = "МСК"
WEBASYST_TITLE = "Webasyst"
WEBASYST_VALUE = "335"
SKU_PREFIX = "335-"
REPORT = HERE / "last_b2b335_webasyst_sync.json"
WRITE_DELAY = float(os.getenv("B2B335_WEBASYST_WRITE_DELAY", "0.60"))
MONEY = Decimal("0.01")


def s(v):
    return str(v or "").strip()


def norm(v):
    return re.sub(
        r"[^0-9a-zа-яё]+",
        "",
        unicodedata.normalize("NFKC", s(v)).casefold(),
    )


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


TRANSLIT = str.maketrans({
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z","и":"i","й":"y",
    "к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f",
    "х":"h","ц":"ts","ч":"ch","ш":"sh","щ":"sch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya",
})


def product_url(name, sku):
    text = unicodedata.normalize("NFKC", s(name)).casefold().translate(TRANSLIT)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    sku_part = re.sub(r"[^a-z0-9]+", "-", s(sku).casefold()).strip("-")
    base = text[:140].strip("-") or "b2b-fabrika"
    return (base + "-" + sku_part).strip("-")


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


def exact_one(rows, wanted, *, label):
    matches = [
        row for row in rows
        if norm(row.get("name") or row.get("title")) == norm(wanted)
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one {label} {wanted!r}; found {len(matches)}")
    return matches[0]


def load_kit_module():
    path = ROOT / "kenner-kit" / "sync_kenner_kit.py"
    spec = importlib.util.spec_from_file_location("b2b335_kit_sync_module", path)
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


def desired_prices(variant):
    pricing = variant.get("pricing") or {}
    customer = money(pricing.get("manual_discount_price") or pricing.get("final_price"))
    compare = money(pricing.get("price"))
    if customer is None or customer <= 0:
        return None
    if compare is None or compare < customer:
        compare = Decimal("0.00")
    return customer, compare


def product_skus(product):
    skus = product.get("skus")
    if isinstance(skus, dict):
        return [x for x in skus.values() if isinstance(x, dict)]
    if isinstance(skus, list):
        return [x for x in skus if isinstance(x, dict)]
    return []


def get_product_skus(wa, product_id):
    payload = wa.call("shop.product.skus.getList", params={"product_id": product_id})
    return listify(payload, ("skus", "items"))


def extract_product_id(payload):
    if isinstance(payload, dict):
        for key in ("id", "product_id"):
            if payload.get(key) not in (None, ""):
                return s(payload.get(key))
        product = payload.get("product")
        if isinstance(product, dict) and product.get("id") not in (None, ""):
            return s(product.get("id"))
    return ""


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


def extimg_summary(urls):
    urls = [s(x) for x in urls if s(x)]
    if not urls:
        return ""
    return "[extimg]\n" + "\n".join(urls) + "\n[/extimg]"


def feature_code(title):
    return "b2b335_" + hashlib.sha1(norm(title).encode("utf-8")).hexdigest()[:12]


def main():
    started = now_iso()
    kit_mod = load_kit_module()
    kit = kit_mod.KitClient(s(os.getenv("YANDEX_KIT_TOKEN")))
    wa = WebasystClient(min_request_interval=WRITE_DELAY)

    report = {
        "started_at": started,
        "source": "Yandex KIT only",
        "routing_characteristic": "Webasyst=335",
        "type_name": TYPE_NAME,
        "type_id": None,
        "webasyst_stock": WA_STOCK_NAME,
        "webasyst_stock_id": None,
        "kit_stock": KIT_STOCK_NAME,
        "kit_stock_id": None,
        "kit_variants_scanned": 0,
        "kit_webasyst_335_variants": 0,
        "kit_duplicate_skus": 0,
        "webasyst_products": 0,
        "webasyst_duplicate_skus": 0,
        "processed": 0,
        "created": 0,
        "updated_prices_stock": 0,
        "summaries_updated_from_kit": 0,
        "kit_variants_without_images": 0,
        "unchanged": 0,
        "features_created": 0,
        "feature_values_written": 0,
        "file_urls_resolved": 0,
        "image_source": "Yandex KIT media only",
        "webasyst_routing": "Webasyst=335",
        "errors": [],
        "sample_new": [],
        "sample_updates": [],
        "complete": False,
    }

    # Resolve target Webasyst type and warehouse.
    types = listify(wa.call("shop.type.getList"))
    target_type = exact_one(types, TYPE_NAME, label="Webasyst product type")
    type_id = s(target_type.get("id"))
    report["type_id"] = type_id

    wa_stocks = listify(wa.call("shop.stock.getList"))
    wa_stock = exact_one(wa_stocks, WA_STOCK_NAME, label="Webasyst stock")
    wa_stock_id = s(wa_stock.get("id"))
    if wa_stock_id != EXPECTED_WA_STOCK_ID:
        raise RuntimeError(
            f"Safety stop: stock {WA_STOCK_NAME!r} changed id from {EXPECTED_WA_STOCK_ID} to {wa_stock_id}"
        )
    report["webasyst_stock_id"] = wa_stock_id

    # Resolve KIT warehouse and routing characteristic.
    kit_warehouses = kit.list_all("/v1/warehouses", {"status": "ACTIVE"}, "warehouses")
    kit_stock = exact_one(kit_warehouses, KIT_STOCK_NAME, label="KIT warehouse")
    kit_stock_id = s(kit_stock.get("id"))
    report["kit_stock_id"] = kit_stock_id

    kit_chars = kit.list_all("/v1/characteristics", {"status": "ACTIVE"}, "characteristics")
    char_by_id = {s(x.get("id")): x for x in kit_chars if s(x.get("id"))}
    webasyst_char = exact_one(kit_chars, WEBASYST_TITLE, label="KIT characteristic")
    webasyst_char_id = s(webasyst_char.get("id"))

    # Scan KIT only. Webasyst=335 is the routing gate; SKU is the exact match key.
    kit_by_sku = defaultdict(list)
    # Restrict the KIT scan to the managed SKU namespace first, then apply
    # Webasyst=335 as the authoritative routing gate.
    for variant in kit.list_all("/v1/variants", {"name": SKU_PREFIX}, "variants"):
        report["kit_variants_scanned"] += 1
        if s(variant.get("status")).upper() == "ARCHIVED":
            continue
        if char_value(variant, webasyst_char_id) != WEBASYST_VALUE:
            continue
        report["kit_webasyst_335_variants"] += 1
        sku = s(variant.get("sku"))
        if not sku.startswith(SKU_PREFIX):
            report["errors"].append({
                "stage": "kit_route",
                "variant_id": s(variant.get("id")),
                "sku": sku,
                "message": "Webasyst=335 variant does not use SKU prefix 335-",
            })
            continue
        kit_by_sku[sku].append(variant)

    report["kit_duplicate_skus"] = sum(1 for rows in kit_by_sku.values() if len(rows) > 1)
    kit_unique = {sku: rows[0] for sku, rows in kit_by_sku.items() if len(rows) == 1}

    # Feature lookup. The supplier routing gate must exist in BOTH systems:
    # KIT Webasyst=335 <-> Webasyst Webasyst=335.
    type_features = listify(
        wa.call("shop.feature.getList", params={"type_id": type_id}),
        ("features", "items"),
    )
    all_features = listify(wa.call("shop.feature.getList"), ("features", "items"))
    webasyst_feature_matches = [
        row for row in (type_features + all_features)
        if norm(row.get("name") or row.get("title")) == norm(WEBASYST_TITLE)
        and s(row.get("code"))
    ]
    # De-duplicate same feature returned in type + global lists.
    wa_webasyst_by_code = {s(row.get("code")): row for row in webasyst_feature_matches}
    if len(wa_webasyst_by_code) != 1:
        raise RuntimeError(
            f"Expected exactly one Webasyst characteristic in Webasyst; found {len(wa_webasyst_by_code)}"
        )
    wa_webasyst_code = next(iter(wa_webasyst_by_code))
    report["webasyst_routing_feature_code"] = wa_webasyst_code

    # Existing managed Webasyst cards: type 203 is only a safety boundary.
    # Actual supplier membership is Webasyst=335.
    products = load_wa_products(wa, type_id)
    report["webasyst_products"] = len(products)
    wa_by_sku = defaultdict(list)
    managed_wa_products = 0
    for product in products:
        product_id = s(product.get("id"))
        if not product_id:
            continue
        info = wa.call("shop.product.getInfo", params={"id": product_id})
        features = (info or {}).get("features") or {}
        if s(features.get(wa_webasyst_code)) != WEBASYST_VALUE:
            continue
        managed_wa_products += 1
        for sku_row in product_skus(product):
            sku = s(sku_row.get("sku"))
            if sku:
                wa_by_sku[sku].append((product, sku_row))
    report["webasyst_335_products"] = managed_wa_products
    report["webasyst_duplicate_skus"] = sum(1 for rows in wa_by_sku.values() if len(rows) > 1)

    # Feature lookup for new products.
    type_features = listify(
        wa.call("shop.feature.getList", params={"type_id": type_id}),
        ("features", "items"),
    )
    all_features = listify(wa.call("shop.feature.getList"), ("features", "items"))
    type_by_title = defaultdict(list)
    global_by_title = defaultdict(list)
    for row in type_features:
        type_by_title[norm(row.get("name") or row.get("title"))].append(row)
    for row in all_features:
        global_by_title[norm(row.get("name") or row.get("title"))].append(row)
    feature_cache = {}

    def usable_feature(rows):
        rows = [
            row for row in rows
            if s(row.get("code"))
            and not bool(int(row.get("selectable") or 0))
            and s(row.get("type")).lower() in ("", "varchar", "text")
        ]
        return rows[0] if len(rows) == 1 else None

    def ensure_feature(title):
        key = norm(title)
        if key in feature_cache:
            return feature_cache[key]
        row = usable_feature(type_by_title.get(key, []))
        if row is None:
            row = usable_feature(global_by_title.get(key, []))
        if row is None:
            code = feature_code(title)
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
                row = {
                    "code": code,
                    "name": title,
                    "type": "varchar",
                    "selectable": 0,
                }
            all_features.append(row)
            global_by_title[key].append(row)
            report["features_created"] += 1
        code = s(row.get("code"))
        if not code:
            raise RuntimeError(f"Feature {title!r} has no code")
        feature_cache[key] = code
        return code

    file_url_cache = {}

    def image_urls(variant):
        result = []
        media = sorted(
            [x for x in (variant.get("media") or []) if isinstance(x, dict) and s(x.get("type")).upper() == "IMAGE"],
            key=lambda x: int(x.get("display_sequence") or 0),
        )
        for row in media:
            file_id = s(row.get("image_id"))
            if not file_id:
                continue
            if file_id not in file_url_cache:
                payload = kit.request("GET", f"/v1/files/{file_id}")
                file_url_cache[file_id] = s(payload.get("url"))
                if file_url_cache[file_id]:
                    report["file_urls_resolved"] += 1
            if file_url_cache[file_id]:
                result.append(file_url_cache[file_id])
        return result

    def source_features(variant):
        out = {}
        for entry in variant.get("characteristics") or []:
            cid = s(entry.get("characteristic_id"))
            title = s((char_by_id.get(cid) or {}).get("title"))
            if not title or norm(title) == norm("Ссылка поставщика"):
                continue
            value = char_value(variant, cid)
            if value:
                out.setdefault(title, value)
        if s(variant.get("brand")):
            out.setdefault("Бренд", s(variant.get("brand")))
        out.setdefault(WEBASYST_TITLE, WEBASYST_VALUE)
        return out

    for sku, variant in sorted(kit_unique.items()):
        try:
            # Exact item match only inside the already-routed Webasyst=335 population.
            matches = wa_by_sku.get(sku, [])
            if len(matches) > 1:
                raise RuntimeError(f"duplicate Webasyst SKU in type {type_id}: {sku}")

            prices = desired_prices(variant)
            if prices is None:
                raise RuntimeError("KIT customer price is empty or zero")
            sale, compare = prices
            stock = kit_stock_qty(variant, kit_stock_id)

            if matches:
                # Existing Webasyst product:
                # - prices/stock come from KIT;
                # - short description image links are refreshed from KIT media.
                product, sku_row = matches[0]
                product_id = s(product.get("id"))

                summary_changed = False
                urls = image_urls(variant)
                if urls:
                    desired_summary = extimg_summary(urls)
                    current_summary = s(product.get("summary"))
                    if current_summary != desired_summary:
                        wa.call(
                            "shop.product.update",
                            http_method="POST",
                            params={"id": product_id},
                            data={"summary": desired_summary},
                        )
                        report["summaries_updated_from_kit"] += 1
                        summary_changed = True
                else:
                    # Never erase an existing summary merely because KIT has no media.
                    report["kit_variants_without_images"] += 1

                changes = {}
                if money(sku_row.get("price")) != sale:
                    changes["price"] = money_str(sale)
                if money(sku_row.get("compare_price")) != compare:
                    changes["compare_price"] = money_str(compare)
                current_stock = wa_stock_qty(sku_row, wa_stock_id)
                if current_stock is None or current_stock != stock:
                    changes["stock"] = {wa_stock_id: str(stock)}

                if changes:
                    wa.call(
                        "shop.product.skus.update",
                        http_method="POST",
                        params={"id": s(sku_row.get("id"))},
                        data=changes,
                    )
                    report["updated_prices_stock"] += 1

                if changes or summary_changed:
                    if len(report["sample_updates"]) < 20:
                        fields = sorted(changes)
                        if summary_changed:
                            fields.append("summary_images_from_kit")
                        report["sample_updates"].append({
                            "sku": sku,
                            "fields": fields,
                            "price": money_str(sale),
                            "compare_price": money_str(compare),
                            "stock": stock,
                            "images_in_summary": len(urls),
                        })
                else:
                    report["unchanged"] += 1
                report["processed"] += 1
                continue

            # New Webasyst product: all content comes from KIT.
            features = {}
            for title, value in source_features(variant).items():
                features[ensure_feature(title)] = value

            urls = image_urls(variant)
            summary = extimg_summary(urls)
            name = s(variant.get("name")) or sku
            description = s(variant.get("description"))
            readable_url = product_url(name, sku)

            created = wa.call(
                "shop.product.add",
                http_method="POST",
                data={
                    "name": name,
                    "url": readable_url,
                    "type_id": type_id,
                    "currency": "RUB",
                    "summary": summary,
                    "description": description,
                    "status": 1 if s(variant.get("status")).upper() == "PUBLISHED" else 0,
                    # No category by design.
                    "features": features,
                    "skus": [{
                        "price": money_str(sale),
                        "compare_price": money_str(compare),
                        "stock": {wa_stock_id: str(stock)},
                        "available": 1,
                        "status": 1,
                    }],
                },
            )
            product_id = extract_product_id(created)
            if not product_id:
                raise RuntimeError(f"shop.product.add did not return product id: {str(created)[:300]}")

            skus = get_product_skus(wa, product_id)
            if len(skus) != 1:
                raise RuntimeError(f"New product {product_id} unexpectedly has {len(skus)} SKUs")
            wa.call(
                "shop.product.skus.update",
                http_method="POST",
                params={"id": s(skus[0].get("id"))},
                data={
                    "sku": sku,
                    "price": money_str(sale),
                    "compare_price": money_str(compare),
                    "stock": {wa_stock_id: str(stock)},
                    "available": 1,
                    "status": 1,
                },
            )

            report["created"] += 1
            report["feature_values_written"] += len(features)
            report["processed"] += 1
            wa_by_sku[sku].append(({"id": product_id}, skus[0]))
            if len(report["sample_new"]) < 20:
                report["sample_new"].append({
                    "sku": sku,
                    "product_id": product_id,
                    "name": name,
                    "url": readable_url,
                    "price": money_str(sale),
                    "compare_price": money_str(compare),
                    "stock": stock,
                    "features": len(features),
                    "images_in_summary": len(urls),
                })

        except Exception as exc:
            report["errors"].append({
                "sku": sku,
                "variant_id": s(variant.get("id")),
                "message": str(exc)[:1000],
            })

    report["finished_at"] = now_iso()
    report["status"] = "ok" if not report["errors"] else "degraded"
    report["complete"] = (
        report["processed"] == len(kit_unique)
        and not report["errors"]
        and report["kit_duplicate_skus"] == 0
        and report["webasyst_duplicate_skus"] == 0
    )
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
