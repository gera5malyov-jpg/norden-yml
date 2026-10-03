from __future__ import annotations

import re
from dataclasses import asdict
from typing import Iterable

from webasyst.client import WebasystClient
from .models import Product


def _listify(payload, keys=("products", "items", "skus")):
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                return [x for x in value.values() if isinstance(x, dict)]
        if payload and all(isinstance(v, dict) for v in payload.values()):
            return list(payload.values())
    return []


def _money(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _num_string(value):
    if value is None:
        return ""
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return ("%.4f" % value).rstrip("0").rstrip(".")


def _slug(text):
    value = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]+", "-", str(text or "").strip(), flags=re.UNICODE)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:120] or "product"


def extimg_summary(urls):
    clean = [str(x).strip() for x in (urls or []) if str(x).strip()]
    return "" if not clean else "[extimg]\n" + "\n".join(clean) + "\n[/extimg]"


def index_by_sku(wa: WebasystClient):
    out = {}
    offset = 0
    while True:
        payload = wa.call(
            "shop.product.search",
            params={"offset": offset, "limit": 1000, "fields": "id,name,summary,type_id,skus"},
        )
        rows = _listify(payload)
        for product in rows:
            skus = product.get("skus") or []
            if isinstance(skus, dict):
                skus = list(skus.values())
            for sku in skus:
                code = str(sku.get("sku") or "").strip()
                if code:
                    out.setdefault(code, []).append((product, sku))
        if len(rows) < 1000:
            break
        offset += len(rows)
    return out


def previous_prices(existing):
    out = {}
    for code, matches in existing.items():
        if len(matches) != 1:
            continue
        _, sku = matches[0]
        out[code] = {
            "purchase_price": _money(sku.get("purchase_price")),
            "price": _money(sku.get("price")),
            "compare_price": _money(sku.get("compare_price")),
        }
    return out


def build_plan(products: Iterable[Product], existing, links=None, rules=None):
    rules = rules or {}
    links = links or []
    products = list(products)
    plan = {"create": [], "update": [], "zero": [], "skipped": [], "blocked": []}
    seen_supplier = set()

    for product in products:
        seen_supplier.add(product.supplier_sku)
        matches = existing.get(product.sku, [])
        desired = asdict(product)
        if len(matches) > 1:
            plan["blocked"].append({
                "sku": product.sku,
                "reason": "duplicate_webasyst_sku",
                "matches": len(matches),
            })
            continue
        if len(matches) == 1:
            current_product, current_sku = matches[0]
            plan["update"].append({
                "sku": product.sku,
                "supplier_sku": product.supplier_sku,
                "product_id": current_product.get("id"),
                "sku_id": current_sku.get("id"),
                "current_product": current_product,
                "current_sku": current_sku,
                "desired": desired,
            })
            continue

        if not rules.get("create_new", False):
            plan["skipped"].append({"sku": product.sku, "reason": "create_disabled"})
            continue
        if rules.get("only_create_in_stock", False) and (product.stock is None or product.stock <= 0):
            plan["skipped"].append({"sku": product.sku, "reason": "not_in_stock"})
            continue
        plan["create"].append({"sku": product.sku, "supplier_sku": product.supplier_sku, "desired": desired})

    if rules.get("zero_if_missing", False) and rules.get("update_stock", True):
        for link in links:
            supplier_sku = str(link.get("supplier_sku") or "").strip()
            sku_id = int(link.get("sku_id") or 0)
            product_id = int(link.get("product_id") or 0)
            if supplier_sku and supplier_sku not in seen_supplier and sku_id and product_id:
                plan["zero"].append({
                    "supplier_sku": supplier_sku,
                    "sku_id": sku_id,
                    "product_id": product_id,
                })

    return plan


def validate_apply_plan(plan, config):
    errors = []
    web = config.get("webasyst") or {}
    rules = config.get("rules") or {}
    if plan["blocked"]:
        errors.append("В Webasyst найдены дубли итоговых артикулов.")
    if plan["create"] and not web.get("type_id"):
        errors.append("Для создания новых товаров не задан webasyst.type_id.")
    needs_stock = rules.get("update_stock", True) and (
        any(row["desired"].get("stock") is not None for row in plan["create"] + plan["update"]) or bool(plan["zero"])
    )
    if needs_stock and not web.get("stock_id"):
        errors.append("Для изменения остатков не задан webasyst.stock_id.")
    if rules.get("update_characteristics", False):
        missing = set()
        mapping = web.get("feature_codes") or {}
        for row in plan["create"] + plan["update"]:
            for key, value in (row["desired"].get("characteristics") or {}).items():
                if value not in (None, "") and not mapping.get(key):
                    missing.add(key)
        if missing:
            errors.append("Не заданы коды характеристик Webasyst: " + ", ".join(sorted(missing)))
    return errors


def _sku_write_data(desired, rules, stock_id):
    data = {}
    if rules.get("update_prices", True):
        for field in ("purchase_price", "price", "compare_price"):
            if desired.get(field) is not None:
                data[field] = _num_string(desired[field])
    if rules.get("update_stock", True) and desired.get("stock") is not None:
        data["stock"] = {str(int(stock_id)): _num_string(desired["stock"])}
        data["available"] = 1 if desired["stock"] > 0 else 0
    return data


def _product_write_data(desired, rules, web, *, creating=False):
    data = {}
    if creating or rules.get("update_name", False):
        data["name"] = desired.get("name") or desired.get("sku") or "Товар"
    if (creating or rules.get("update_images", False)) and desired.get("images"):
        data["summary"] = extimg_summary(desired["images"])
    if creating or rules.get("update_characteristics", False):
        feature_codes = web.get("feature_codes") or {}
        features = {}
        for key, value in (desired.get("characteristics") or {}).items():
            code = feature_codes.get(key)
            if code and value not in (None, ""):
                features[str(code)] = value
        if desired.get("brand") and web.get("brand_feature_code"):
            features[str(web["brand_feature_code"])] = desired["brand"]
        if features:
            data["features"] = features
    if creating and web.get("category_id"):
        data["categories"] = [int(web["category_id"])]
    return data


def _extract_product_id(payload):
    if isinstance(payload, dict):
        for key in ("id", "product_id"):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        for key in ("product", "data", "result"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                value = _extract_product_id(nested)
                if value:
                    return value
    return ""


class ApplyError(RuntimeError):
    def __init__(self, message, result):
        super().__init__(message)
        self.result = result


def apply_plan(wa: WebasystClient, plan, config):
    errors = validate_apply_plan(plan, config)
    if errors:
        raise ApplyError("; ".join(errors), {"created": 0, "updated": 0, "zeroed": 0, "mappings": []})

    rules = config.get("rules") or {}
    web = config.get("webasyst") or {}
    stock_id = web.get("stock_id")
    result = {"created": 0, "updated": 0, "zeroed": 0, "mappings": []}

    try:
        for row in plan["update"]:
            desired = row["desired"]
            sku_data = _sku_write_data(desired, rules, stock_id)
            if sku_data:
                wa.call(
                    "shop.product.skus.update",
                    http_method="POST",
                    params={"id": str(row["sku_id"])},
                    data=sku_data,
                )
            product_data = _product_write_data(desired, rules, web, creating=False)
            if product_data:
                wa.call(
                    "shop.product.update",
                    http_method="POST",
                    params={"id": str(row["product_id"])},
                    data=product_data,
                )
            result["updated"] += 1
            result["mappings"].append({
                "supplier_sku": row["supplier_sku"],
                "product_id": int(row["product_id"]),
                "sku_id": int(row["sku_id"]),
                "purchase_price": desired.get("purchase_price"),
                "stock": desired.get("stock"),
            })

        for row in plan["create"]:
            desired = row["desired"]
            sku_data = _sku_write_data(desired, rules, stock_id)
            sku_data.update({"available": 1 if (desired.get("stock") or 0) > 0 else 0, "status": 1})
            product_data = _product_write_data(desired, rules, web, creating=True)
            product_data.update({
                "type_id": int(web["type_id"]),
                "currency": str(web.get("currency") or "RUB"),
                "status": int(web.get("new_product_status", 0)),
                "url": _slug(desired.get("name") or row["sku"]),
                "skus": [sku_data],
            })
            created = wa.call("shop.product.add", http_method="POST", data=product_data)
            product_id = _extract_product_id(created)
            if not product_id:
                raise RuntimeError("Webasyst не вернул ID созданного товара %s" % row["sku"])
            skus = _listify(wa.call("shop.product.skus.getList", params={"product_id": product_id}))
            if len(skus) != 1:
                raise RuntimeError("У созданного товара %s найдено %d SKU вместо 1" % (row["sku"], len(skus)))
            sku_id = str(skus[0].get("id") or "")
            if not sku_id:
                raise RuntimeError("Webasyst не вернул SKU ID товара %s" % row["sku"])
            wa.call(
                "shop.product.skus.update",
                http_method="POST",
                params={"id": sku_id},
                data={"sku": row["sku"]},
            )
            result["created"] += 1
            result["mappings"].append({
                "supplier_sku": row["supplier_sku"],
                "product_id": int(product_id),
                "sku_id": int(sku_id),
                "purchase_price": desired.get("purchase_price"),
                "stock": desired.get("stock"),
            })

        if rules.get("zero_if_missing", False) and rules.get("update_stock", True):
            for row in plan["zero"]:
                wa.call(
                    "shop.product.skus.update",
                    http_method="POST",
                    params={"id": str(row["sku_id"])},
                    data={"stock": {str(int(stock_id)): "0"}, "available": 0},
                )
                result["zeroed"] += 1
                result["mappings"].append({
                    "supplier_sku": row["supplier_sku"],
                    "product_id": int(row["product_id"]),
                    "sku_id": int(row["sku_id"]),
                    "purchase_price": None,
                    "stock": 0,
                })
    except Exception as exc:
        raise ApplyError(str(exc), result) from exc

    return result
