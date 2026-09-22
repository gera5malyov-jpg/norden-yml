#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from client import WebasystClient

TYPE_NAME = "aletan.ru"
WA_STOCK_NAME = "Основной склад"
WA_STOCK_QTY = 100
REPORT = HERE / "last_aletan_webasyst_sync.json"
WRITE_DELAY = float(os.getenv("ALETAN_WEBASYST_WRITE_DELAY", "0.65"))
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
    return d.quantize(MONEY, rounding=ROUND_HALF_UP) if d >= 0 else None


def money_str(v):
    d = money(v)
    return None if d is None else f"{d:.2f}"


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
    matches = [
        row for row in rows
        if norm(row.get("name") or row.get("title")) == norm(wanted)
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Ожидался ровно один {label} {wanted!r}; найдено {len(matches)}")
    return matches[0]


def load_catalog_module():
    path = ROOT / "aletan-kit" / "sync_aletan_catalog_full.py"
    spec = importlib.util.spec_from_file_location("aletan_catalog_full", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


TRANSLIT = str.maketrans({
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z","и":"i","й":"y",
    "к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f",
    "х":"h","ц":"ts","ч":"ch","ш":"sh","щ":"sch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya",
})


def product_url(name, code):
    text = unicodedata.normalize("NFKC", s(name)).casefold().translate(TRANSLIT)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    code_part = re.sub(r"[^a-z0-9]+", "-", s(code).casefold()).strip("-")
    return ((text[:140].strip("-") or "aletan") + "-" + code_part).strip("-")


def extimg_summary(urls):
    urls = [s(x) for x in urls if s(x)]
    if not urls:
        return ""
    return "[extimg]\n" + "\n".join(urls) + "\n[/extimg]"


def candidate_codes_from_product(product, source_codes):
    exact = []
    for sku in product_skus(product):
        code = s(sku.get("sku"))
        if code in source_codes:
            exact.append(code)
    if exact:
        return list(dict.fromkeys(exact))

    name = s(product.get("name"))
    tokens = re.findall(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё._/-]*", name)
    found = []
    for token in tokens:
        if token in source_codes:
            found.append(token)
        stripped = token.strip("()[]{}.,;:!?")
        if stripped in source_codes:
            found.append(stripped)
    return list(dict.fromkeys(found))


def desired_prices(variant):
    pricing = variant.get("pricing") or {}
    sale = money(pricing.get("manual_discount_price") or pricing.get("final_price"))
    compare = money(pricing.get("price"))
    if sale is None or sale <= 0:
        return None
    if compare is None or compare < sale:
        compare = Decimal("0.00")
    return sale, compare


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    catalog_mod = load_catalog_module()
    base = catalog_mod.base

    report = {
        "status": "running",
        "dry_run": bool(args.dry_run),
        "type_name": TYPE_NAME,
        "webasyst_stock": WA_STOCK_NAME,
        "webasyst_stock_qty": WA_STOCK_QTY,
        "match_rule": "vendorCode: exact SKU first, then exact token in product name",
        "created": 0,
        "would_create": 0,
        "updated": 0,
        "would_update": 0,
        "kit_matched": 0,
        "webasyst_products_before": 0,
        "webasyst_ambiguous_codes": [],
        "kit_ambiguous_codes": [],
        "errors": [],
        "warnings": [],
        "sample_new": [],
        "sample_updates": [],
        "complete": False,
    }

    token = s(os.getenv("YANDEX_KIT_TOKEN"))
    if not token:
        raise RuntimeError("YANDEX_KIT_TOKEN is not configured")

    catalog, duplicates, _ = catalog_mod.load_catalog_full()
    source_codes = set(catalog)
    report["catalog_unique_vendor_codes"] = len(catalog)
    report["catalog_duplicate_vendor_codes"] = sorted(duplicates)[:100]

    kit = catalog_mod.KitExt(token)
    kit_index, kit_ambiguous, _, _, _ = base.build_kit_index(kit, source_codes)
    report["kit_matched"] = len(kit_index)
    report["kit_ambiguous_codes"] = sorted(kit_ambiguous)[:100]

    wa = WebasystClient(min_request_interval=WRITE_DELAY)
    types = listify(wa.call("shop.type.getList"))
    target_type = exact_one(types, TYPE_NAME, "тип товара Webasyst")
    type_id = s(target_type.get("id"))
    report["type_id"] = type_id

    stocks = listify(wa.call("shop.stock.getList"))
    stock = exact_one(stocks, WA_STOCK_NAME, "склад Webasyst")
    stock_id = s(stock.get("id"))
    report["webasyst_stock_id"] = stock_id

    wa_products = load_wa_products(wa, type_id)
    report["webasyst_products_before"] = len(wa_products)

    wa_by_code = defaultdict(list)
    ambiguous_products = []
    for product in wa_products:
        candidates = candidate_codes_from_product(product, source_codes)
        if len(candidates) > 1:
            ambiguous_products.append({
                "product_id": s(product.get("id")),
                "name": s(product.get("name")),
                "candidate_codes": candidates,
            })
            continue
        if len(candidates) == 1:
            wa_by_code[candidates[0]].append(product)

    report["ambiguous_webasyst_products"] = ambiguous_products[:100]
    report["webasyst_ambiguous_codes"] = sorted(
        code for code, rows in wa_by_code.items() if len(rows) > 1
    )[:100]

    for code, variant in sorted(kit_index.items()):
        if code not in catalog or code in kit_ambiguous:
            continue
        if len(wa_by_code.get(code, [])) > 1:
            report["errors"].append({
                "vendor_code": code,
                "stage": "match",
                "message": "В Webasyst найдено несколько товаров типа aletan.ru с одним vendorCode",
            })
            continue

        detail = variant
        if "pricing" not in detail:
            detail = kit.get_variant(s(variant.get("id")))
        prices = desired_prices(detail)
        if not prices:
            report["warnings"].append({
                "vendor_code": code,
                "stage": "price",
                "message": "Пропущено: в KIT нет корректной цены для покупателя",
            })
            continue
        sale, compare = prices
        item = catalog[code]
        matches = wa_by_code.get(code, [])

        if matches:
            product = matches[0]
            product_id = s(product.get("id"))
            skus = product_skus(product) or get_product_skus(wa, product_id)
            if len(skus) != 1:
                report["errors"].append({
                    "vendor_code": code,
                    "product_id": product_id,
                    "stage": "sku",
                    "message": f"У товара Webasyst ожидается 1 SKU, найдено {len(skus)}",
                })
                continue
            sku = skus[0]
            if args.dry_run:
                report["would_update"] += 1
            else:
                wa.call(
                    "shop.product.update",
                    http_method="POST",
                    params={"id": product_id},
                    data={"type_id": type_id, "status": 1},
                )
                wa.call(
                    "shop.product.skus.update",
                    http_method="POST",
                    params={"id": s(sku.get("id"))},
                    data={
                        "price": money_str(sale),
                        "compare_price": money_str(compare),
                        "stock": {stock_id: str(WA_STOCK_QTY)},
                        "available": 1,
                        "status": 1,
                    },
                )
                report["updated"] += 1
            if len(report["sample_updates"]) < 25:
                report["sample_updates"].append({
                    "vendor_code": code,
                    "product_id": product_id,
                    "existing_sku": s(sku.get("sku")),
                    "price": money_str(sale),
                    "compare_price": money_str(compare),
                    "stock": WA_STOCK_QTY,
                })
            continue

        summary = extimg_summary(item["pictures"])
        if args.dry_run:
            report["would_create"] += 1
        else:
            created = wa.call(
                "shop.product.add",
                http_method="POST",
                data={
                    "name": item["name"],
                    "url": product_url(item["name"], code),
                    "type_id": type_id,
                    "currency": "RUB",
                    "summary": summary,
                    "description": item["description"],
                    "status": 1,
                    "skus": [{
                        "price": money_str(sale),
                        "compare_price": money_str(compare),
                        "stock": {stock_id: str(WA_STOCK_QTY)},
                        "available": 1,
                        "status": 1,
                    }],
                },
            )
            product_id = extract_product_id(created)
            if not product_id:
                raise RuntimeError(f"{code}: shop.product.add не вернул product_id")
            skus = get_product_skus(wa, product_id)
            if len(skus) != 1:
                raise RuntimeError(f"{code}: новый товар имеет {len(skus)} SKU вместо 1")
            wa.call(
                "shop.product.skus.update",
                http_method="POST",
                params={"id": s(skus[0].get("id"))},
                data={
                    "sku": code,
                    "price": money_str(sale),
                    "compare_price": money_str(compare),
                    "stock": {stock_id: str(WA_STOCK_QTY)},
                    "available": 1,
                    "status": 1,
                },
            )
            report["created"] += 1
            wa_by_code[code].append({"id": product_id, "name": item["name"], "skus": skus})

        if len(report["sample_new"]) < 25:
            report["sample_new"].append({
                "vendor_code": code,
                "sku": code,
                "name": item["name"],
                "price": money_str(sale),
                "compare_price": money_str(compare),
                "stock": WA_STOCK_QTY,
                "images": len(item["pictures"]),
            })

    report["status"] = "ok" if not report["errors"] else "degraded"
    report["complete"] = not report["errors"]
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
