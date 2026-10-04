from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
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


def index_by_sku(wa: WebasystClient, type_id=None):
    out = {}
    offset = 0
    while True:
        params = {"offset": offset, "limit": 1000, "fields": "*,skus,stock_counts"}
        if type_id:
            params["hash"] = "type/%s" % int(type_id)
        payload = wa.call(
            "shop.product.search",
            params=params,
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



_CONFUSABLES = str.maketrans({
    "а":"a","в":"b","с":"c","е":"e","н":"h","к":"k","м":"m","о":"o","р":"p","т":"t","х":"x","у":"y",
    "А":"a","В":"b","С":"c","Е":"e","Н":"h","К":"k","М":"m","О":"o","Р":"p","Т":"t","Х":"x","У":"y",
})


def _identity_key(value):
    text = unicodedata.normalize("NFKC", str(value or "").strip()).translate(_CONFUSABLES).casefold()
    return re.sub(r"[^0-9a-z]+", "", text)


def index_by_supplier_sku_name(wa: WebasystClient, type_id, supplier_codes):
    """Index one Webasyst product type by supplier article stored in SKU name.

    Exact text wins over normalized fallback. This prevents legacy lookalikes
    such as Latin C vs Cyrillic С or '-' vs '*' from becoming false duplicates.
    """
    source_exact = {}
    source_by_key = defaultdict(list)
    for code in supplier_codes or []:
        code = str(code or "").strip()
        key = _identity_key(code)
        if not code or not key:
            continue
        source_exact.setdefault(code, []).append(code)
        source_by_key[key].append(code)

    candidates = defaultdict(list)
    offset = 0
    while True:
        payload = wa.call(
            "shop.product.search",
            params={
                "hash": "type/%s" % type_id,
                "offset": offset,
                "limit": 1000,
                "fields": "*,skus,stock_counts",
            },
        )
        rows = _listify(payload)
        for product in rows:
            skus = product.get("skus") or []
            if isinstance(skus, dict):
                skus = list(skus.values())
            for sku in skus:
                supplier_name = str(sku.get("name") or "").strip()
                internal_sku = str(sku.get("sku") or "").strip()

                exact = source_exact.get(supplier_name) or []
                if len(exact) == 1:
                    candidates[exact[0]].append((0, product, sku))
                    continue

                # Products created by this supplier engine store the supplier
                # article as the final Webasyst SKU. This exact fallback also
                # recovers safely from a cancelled partial apply before links
                # could be synced.
                exact_sku = source_exact.get(internal_sku) or []
                if len(exact_sku) == 1:
                    candidates[exact_sku[0]].append((1, product, sku))
                    continue

                if not supplier_name:
                    continue
                key = _identity_key(supplier_name)
                source_matches = source_by_key.get(key) or []
                # Ambiguous normalized source codes are deliberately not guessed.
                if len(source_matches) == 1:
                    candidates[source_matches[0]].append((2, product, sku))

        if len(rows) < 1000:
            break
        offset += len(rows)

    out = {}
    for source_code, rows in candidates.items():
        best_rank = min(row[0] for row in rows)
        best = [(product, sku) for rank, product, sku in rows if rank == best_rank]
        if best:
            out[source_code] = best
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

    existing_by_ids = {}
    for matches in existing.values():
        for current_product, current_sku in matches:
            product_id = str(current_product.get("id") or "").strip()
            sku_id = str(current_sku.get("id") or "").strip()
            if product_id and sku_id:
                existing_by_ids[(product_id, sku_id)] = (current_product, current_sku)

    links_by_supplier = {}
    valid_links = []
    for link in links:
        supplier_sku = str(link.get("supplier_sku") or "").strip()
        product_id = str(link.get("product_id") or "").strip()
        sku_id = str(link.get("sku_id") or "").strip()
        if not supplier_sku or not product_id or not sku_id:
            continue
        valid_links.append(link)
        links_by_supplier.setdefault(supplier_sku, []).append(link)

    for product in products:
        seen_supplier.add(product.supplier_sku)
        matches = existing.get(product.sku, [])
        desired = asdict(product)

        if not matches and product.supplier_sku:
            supplier_links = links_by_supplier.get(product.supplier_sku, [])
            if len(supplier_links) > 1:
                plan["blocked"].append({
                    "sku": product.sku,
                    "supplier_sku": product.supplier_sku,
                    "reason": "duplicate_supplier_link",
                    "matches": len(supplier_links),
                })
                continue
            if len(supplier_links) == 1:
                link = supplier_links[0]
                key = (str(link.get("product_id")), str(link.get("sku_id")))
                linked = existing_by_ids.get(key)
                if linked is None:
                    plan["blocked"].append({
                        "sku": product.sku,
                        "supplier_sku": product.supplier_sku,
                        "reason": "stale_supplier_link",
                        "product_id": link.get("product_id"),
                        "sku_id": link.get("sku_id"),
                    })
                    continue
                matches = [linked]

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

    if rules.get("zero_if_missing", False) and rules.get("update_stock", True) and valid_links:
        linked_supplier_skus = {str(link.get("supplier_sku") or "").strip() for link in valid_links}
        overlap = linked_supplier_skus & seen_supplier
        updated_ids = {
            (str(row.get("product_id")), str(row.get("sku_id")))
            for row in plan["update"]
        }
        linked_ids = {
            (str(link.get("product_id") or ""), str(link.get("sku_id") or ""))
            for link in valid_links
        }
        matched_link_ids = linked_ids & updated_ids
        effective_matches = max(len(overlap), len(matched_link_ids))
        if len(valid_links) >= 10 and effective_matches / float(len(linked_supplier_skus) or 1) < 0.20:
            plan["blocked"].append({
                "reason": "supplier_link_overlap_too_low",
                "links": len(linked_supplier_skus),
                "matched_links": effective_matches,
            })
        else:
            for link in valid_links:
                supplier_sku = str(link.get("supplier_sku") or "").strip()
                sku_id = int(link.get("sku_id") or 0)
                product_id = int(link.get("product_id") or 0)
                if (str(product_id), str(sku_id)) in updated_ids:
                    continue
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
        reasons = {str(row.get("reason") or "") for row in plan["blocked"]}
        if "duplicate_webasyst_sku" in reasons:
            errors.append("В Webasyst найдены дубли итоговых артикулов.")
        if "duplicate_supplier_link" in reasons:
            errors.append("У поставщика найдены дубли привязок одного артикула.")
        if "stale_supplier_link" in reasons:
            errors.append("Найдены устаревшие привязки поставщика к отсутствующим SKU Webasyst.")
        if "supplier_link_overlap_too_low" in reasons:
            errors.append("Привязки поставщика почти не совпадают с текущим прайсом; массовое обнуление остатков заблокировано.")
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
    # Do not upload image files into Webasyst. Keep external image URLs in
    # the product short description, where the existing [extimg] handler can use them.
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
        is_norden = str((config.get("source") or {}).get("format") or "").lower() == "norden"

        for row in plan["update"]:
            desired = row["desired"]
            current_product = row.get("current_product") or {}
            current_sku = row.get("current_sku") or {}
            sku_data = _sku_write_data(desired, rules, stock_id)

            if is_norden:
                supplier_code = str(row.get("supplier_sku") or "").strip()
                if supplier_code and str(current_sku.get("name") or "").strip() != supplier_code:
                    sku_data["name"] = supplier_code

                # The cancelled old importer wrote the supplier article into
                # Webasyst's SKU-code field. Migrate only those engine-created
                # rows to the new numeric internal article scheme. Historical
                # AF-* and other existing SKU codes are preserved.
                if (
                    supplier_code
                    and str(current_sku.get("sku") or "").strip() == supplier_code
                    and str(row.get("sku_id") or "").isdigit()
                ):
                    sku_data["sku"] = str(row["sku_id"])

            if sku_data:
                wa.call(
                    "shop.product.skus.update",
                    http_method="POST",
                    params={"id": str(row["sku_id"])},
                    data=sku_data,
                )
            product_data = _product_write_data(desired, rules, web, creating=False)
            if (
                str(current_product.get("status") or "0") == "0"
                and str(current_sku.get("sku") or "").strip() == str(row.get("supplier_sku") or "").strip()
            ):
                # A cancelled old apply may have created the card before it
                # could finish/link it. Such engine-created cards are safe to
                # publish on the next successful apply.
                product_data["status"] = 1
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
                "status": 1,
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
            final_sku_data = {"sku": row["sku"]}
            if is_norden:
                # Webasyst SKU ID is already unique and numeric. Use it as the
                # internal article, while keeping the supplier article in the
                # human-readable SKU name ("Наименование артикула").
                final_sku_data = {
                    "sku": sku_id,
                    "name": str(row.get("supplier_sku") or "").strip(),
                }
            wa.call(
                "shop.product.skus.update",
                http_method="POST",
                params={"id": sku_id},
                data=final_sku_data,
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
