from __future__ import annotations

import concurrent.futures
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


def _same_number(left, right):
    a = _money(left)
    b = _money(right)
    return a is not None and b is not None and abs(a - b) < 0.0001


def _value_text(value):
    if isinstance(value, dict):
        for key in ("value", "name", "title"):
            if value.get(key) not in (None, ""):
                return str(value.get(key)).strip()
        values = [_value_text(v) for v in value.values()]
        return " | ".join(x for x in values if x)
    if isinstance(value, (list, tuple)):
        values = [_value_text(v) for v in value]
        return " | ".join(x for x in values if x)
    return str(value or "").strip()


def _current_feature_map(product):
    raw = (product or {}).get("features")
    if isinstance(raw, dict):
        return {str(k): _value_text(v) for k, v in raw.items()}
    if isinstance(raw, list):
        out = {}
        for row in raw:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or row.get("id") or "").strip()
            if not code:
                continue
            values = row.get("values")
            if values not in (None, ""):
                out[code] = _value_text(values)
            else:
                out[code] = _value_text(row.get("value"))
        return out
    return {}


def _current_stock_value(sku, stock_id):
    sid = str(stock_id or "").strip()
    if not sid:
        return None
    for key in ("stock", "stock_counts", "stocks"):
        raw = (sku or {}).get(key)
        if isinstance(raw, dict):
            value = raw.get(sid)
            if value is None and sid.isdigit():
                value = raw.get(int(sid))
            if isinstance(value, dict):
                for value_key in ("count", "stock", "quantity", "value"):
                    if value.get(value_key) not in (None, ""):
                        value = value.get(value_key)
                        break
            if value not in (None, ""):
                return _money(value)
        elif isinstance(raw, list):
            for row in raw:
                if not isinstance(row, dict):
                    continue
                row_id = str(row.get("stock_id") or row.get("warehouse_id") or row.get("id") or "").strip()
                if row_id != sid:
                    continue
                for value_key in ("count", "stock", "quantity", "value"):
                    if row.get(value_key) not in (None, ""):
                        return _money(row.get(value_key))
        elif key == "stock" and raw not in (None, ""):
            return _money(raw)
    return None


def _delta_sku_data(data, current_sku, stock_id):
    if not data:
        return {}
    out = dict(data)
    for field in ("purchase_price", "price", "compare_price"):
        if field in out and _same_number(out[field], (current_sku or {}).get(field)):
            out.pop(field, None)
    if "sku" in out and str(out["sku"]).strip() == str((current_sku or {}).get("sku") or "").strip():
        out.pop("sku", None)
    if "name" in out and str(out["name"]).strip() == str((current_sku or {}).get("name") or "").strip():
        out.pop("name", None)
    if "available" in out:
        current_available = (current_sku or {}).get("available")
        if current_available not in (None, ""):
            try:
                if int(bool(int(current_available))) == int(bool(int(out["available"]))):
                    out.pop("available", None)
            except (TypeError, ValueError):
                pass
    if "stock" in out and isinstance(out["stock"], dict):
        desired_stock = out["stock"]
        stock_equal = True
        for sid, desired in desired_stock.items():
            current = _current_stock_value(current_sku, sid)
            if current is None or not _same_number(current, desired):
                stock_equal = False
                break
        if stock_equal:
            out.pop("stock", None)
    return out


def _delta_product_data(data, current_product):
    if not data:
        return {}
    out = dict(data)
    current_product = current_product or {}
    for field in ("name", "summary", "status"):
        if field not in out:
            continue
        current = current_product.get(field)
        desired = out[field]
        if field == "status":
            try:
                same = int(current) == int(desired)
            except (TypeError, ValueError):
                same = str(current or "").strip() == str(desired or "").strip()
        else:
            same = str(current or "").strip() == str(desired or "").strip()
        if same:
            out.pop(field, None)
    if "features" in out and isinstance(out["features"], dict):
        current_features = _current_feature_map(current_product)
        if current_features:
            same = True
            for code, desired in out["features"].items():
                if _value_text(current_features.get(str(code))) != _value_text(desired):
                    same = False
                    break
            if same:
                out.pop("features", None)
    return out


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


def webasyst_sku_mode(config):
    mode = str((config.get("webasyst") or {}).get("sku_mode") or "").strip().lower()
    if mode in {"numeric", "supplier"}:
        return mode
    return "numeric" if str((config.get("source") or {}).get("format") or "").lower() == "norden" else "supplier"


def index_by_supplier_sku_name(wa: WebasystClient, type_id, supplier_codes, sku_aliases=None, image_aliases=None):
    """Index one Webasyst product type by supplier article stored in SKU name.

    Exact text wins over normalized fallback. This prevents legacy lookalikes
    such as Latin C vs Cyrillic С or '-' vs '*' from becoming false duplicates.
    """
    source_exact = {}
    source_by_key = defaultdict(list)
    sku_aliases = {str(k): str(v) for k, v in (sku_aliases or {}).items() if str(k) and str(v)}
    image_aliases = {str(k): str(v) for k, v in (image_aliases or {}).items() if str(k) and str(v)}
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
            summary_supplier = ""
            if image_aliases:
                summary = str(product.get("summary") or "")
                for url in re.findall(r"https?://[^\s\]<>'\"]+", summary):
                    alias = image_aliases.get(url.rstrip(".,;"))
                    if alias:
                        if summary_supplier and summary_supplier != alias:
                            summary_supplier = ""
                            break
                        summary_supplier = alias
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

                alias_supplier = sku_aliases.get(internal_sku)
                if alias_supplier and len(source_exact.get(alias_supplier) or []) == 1:
                    candidates[alias_supplier].append((1, product, sku))
                    continue

                # Crash recovery: if Webasyst created an AF-* SKU but the
                # follow-up SKU-name/article update was interrupted, the
                # product's [extimg] summary still contains the exact source
                # image URL. Use only unique image→supplier aliases.
                if summary_supplier and len(source_exact.get(summary_supplier) or []) == 1:
                    candidates[summary_supplier].append((2, product, sku))
                    continue

                if not supplier_name:
                    continue
                key = _identity_key(supplier_name)
                source_matches = source_by_key.get(key) or []
                # Ambiguous normalized source codes are deliberately not guessed.
                if len(source_matches) == 1:
                    candidates[source_matches[0]].append((3, product, sku))

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
    plan = {"create": [], "repair_sku": [], "update": [], "zero": [], "skipped": [], "blocked": []}
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
                    # The old supplier link points to a SKU that no longer
                    # exists. The catalog-wide exact search above has already
                    # looked for this supplier article / internal SKU / exact
                    # image alias. If it was moved to another product card,
                    # "matches" would be non-empty and we would update/relink it.
                    # With no current match, restore the missing SKU on the
                    # original product card (or recreate the product if the card
                    # itself is gone).
                    plan["repair_sku"].append({
                        "sku": product.sku,
                        "supplier_sku": product.supplier_sku,
                        "product_id": link.get("product_id"),
                        "old_sku_id": link.get("sku_id"),
                        "desired": desired,
                    })
                    continue
                else:
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
    if (plan["create"] or plan.get("repair_sku")) and not web.get("type_id"):
        errors.append("Для создания новых товаров не задан webasyst.type_id.")
    needs_stock = rules.get("update_stock", True) and (
        any(row["desired"].get("stock") is not None for row in plan["create"] + plan.get("repair_sku", []) + plan["update"]) or bool(plan["zero"])
    )
    if needs_stock and not web.get("stock_id"):
        errors.append("Для изменения остатков не задан webasyst.stock_id.")
    if needs_stock and rules.get("zero_other_stocks", False) and not (web.get("stock_ids") or []):
        errors.append("Для обнуления остальных складов не передан список webasyst.stock_ids.")
    if rules.get("update_characteristics", False) and not rules.get("auto_features", False):
        missing = set()
        mapping = web.get("feature_codes") or {}
        for row in plan["create"] + plan.get("repair_sku", []) + plan["update"]:
            for key, value in (row["desired"].get("characteristics") or {}).items():
                if value not in (None, "") and not mapping.get(key):
                    missing.add(key)
        if missing:
            errors.append("Не заданы коды характеристик Webasyst: " + ", ".join(sorted(missing)))
    return errors


def _sku_write_data(desired, rules, stock_id, stock_ids=None):
    data = {}
    if rules.get("update_prices", True):
        for field in ("purchase_price", "price", "compare_price"):
            if desired.get(field) is not None:
                data[field] = _num_string(desired[field])
    if rules.get("update_stock", True) and desired.get("stock") is not None:
        target_id = str(int(stock_id))
        if rules.get("zero_other_stocks", False):
            ids = [int(x) for x in (stock_ids or []) if str(x).isdigit() and int(x) > 0]
            stock_data = {str(x): "0" for x in ids}
            stock_data[target_id] = _num_string(desired["stock"])
            data["stock"] = stock_data
        else:
            data["stock"] = {target_id: _num_string(desired["stock"])}
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


def _extract_sku_id(payload):
    if isinstance(payload, dict):
        for key in ("id", "sku_id"):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        for key in ("sku", "data", "result"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                value = _extract_sku_id(nested)
                if value:
                    return value
    return ""


class ApplyError(RuntimeError):
    def __init__(self, message, result):
        super().__init__(message)
        self.result = result


def _is_missing_webasyst_sku_error(exc):
    text = str(exc or "").casefold()
    return (
        "http 404" in text
        and (
            "модификация товара не найдена" in text
            or ("invalid_param" in text and "модификац" in text)
        )
    )


def apply_plan(wa: WebasystClient, plan, config):
    errors = validate_apply_plan(plan, config)
    if errors:
        raise ApplyError("; ".join(errors), {"created": 0, "updated": 0, "zeroed": 0, "mappings": []})

    rules = config.get("rules") or {}
    web = config.get("webasyst") or {}
    stock_id = web.get("stock_id")
    stock_ids = web.get("stock_ids") or []
    result = {
        "created": 0,
        "updated": 0,
        "zeroed": 0,
        "recovered_skus": 0,
        "relinked_existing": 0,
        "recreated_products": 0,
        "unchanged_updates": 0,
        "mappings": [],
        "stale_zero_links": 0,
        "stale_zero_sample": [],
    }

    try:
        sku_mode = webasyst_sku_mode(config)

        def add_mapping(row, product_id, sku_id):
            desired = row.get("desired") or {}
            result["mappings"].append({
                "supplier_sku": row["supplier_sku"],
                "product_id": int(product_id),
                "sku_id": int(sku_id),
                "purchase_price": desired.get("purchase_price"),
                "stock": desired.get("stock"),
            })

        def create_full_product(row):
            desired = row["desired"]
            supplier_code = str(row.get("supplier_sku") or "").strip()
            temporary_sku = str(row.get("sku") or supplier_code).strip()
            sku_data = _sku_write_data(desired, rules, stock_id, stock_ids)
            sku_data.update({
                "available": 1 if (desired.get("stock") or 0) > 0 else 0,
                "status": 1,
                "sku": temporary_sku,
                "name": supplier_code,
            })
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
            exact = [
                item for item in skus
                if str(item.get("name") or "").strip() == supplier_code
                or str(item.get("sku") or "").strip() == temporary_sku
            ]
            if len(exact) != 1:
                if len(skus) == 1:
                    exact = skus
                else:
                    raise RuntimeError(
                        "После восстановления товара %s невозможно однозначно определить SKU (%d вариантов)"
                        % (row["sku"], len(skus))
                    )
            sku_id = str(exact[0].get("id") or "")
            if not sku_id:
                raise RuntimeError("Webasyst не вернул SKU ID товара %s" % row["sku"])
            final_sku_data = {
                "sku": sku_id if sku_mode == "numeric" else temporary_sku,
                "name": supplier_code,
            }
            wa.call(
                "shop.product.skus.update",
                http_method="POST",
                params={"id": sku_id},
                data=final_sku_data,
            )
            result["created"] += 1
            return product_id, sku_id

        def find_current_match(row):
            supplier_code = str(row.get("supplier_sku") or "").strip()
            desired_sku = str(row.get("sku") or supplier_code).strip()
            desired = row.get("desired") or {}
            image_aliases = {
                str(url).strip(): supplier_code
                for url in (desired.get("images") or [])
                if str(url).strip()
            }
            found = index_by_supplier_sku_name(
                wa,
                web.get("type_id"),
                [supplier_code],
                {desired_sku: supplier_code} if desired_sku else {},
                image_aliases,
            )
            return list(found.get(supplier_code) or [])

        def restore_missing_sku(row):
            # Re-scan the current catalog immediately before creating anything.
            # If the user moved/corrected the SKU into another product card,
            # reuse that exact current SKU and only relink the supplier.
            matches = find_current_match(row)
            if len(matches) > 1:
                raise RuntimeError(
                    "В Webasyst найдено несколько текущих SKU для артикула поставщика %s; восстановление заблокировано"
                    % row.get("supplier_sku")
                )
            if len(matches) == 1:
                current_product, current_sku = matches[0]
                product_id = str(current_product.get("id") or "")
                sku_id = str(current_sku.get("id") or "")
                if not product_id or not sku_id:
                    raise RuntimeError("Найденный SKU Webasyst не содержит product_id/sku_id")
                desired = row["desired"]
                sku_data = _sku_write_data(desired, rules, stock_id, stock_ids)
                supplier_code = str(row.get("supplier_sku") or "").strip()
                desired_sku = str(row.get("sku") or supplier_code).strip()
                current_code = str(current_sku.get("sku") or "").strip()
                if supplier_code and str(current_sku.get("name") or "").strip() != supplier_code:
                    sku_data["name"] = supplier_code
                if sku_mode == "supplier" and desired_sku and current_code.isdigit():
                    sku_data["sku"] = desired_sku
                if sku_data:
                    wa.call(
                        "shop.product.skus.update",
                        http_method="POST",
                        params={"id": sku_id},
                        data=sku_data,
                    )
                product_data = _product_write_data(desired, rules, web, creating=False)
                if product_data:
                    wa.call(
                        "shop.product.update",
                        http_method="POST",
                        params={"id": product_id},
                        data=product_data,
                    )
                result["relinked_existing"] += 1
                add_mapping(row, product_id, sku_id)
                return

            original_product_id = str(row.get("product_id") or "").strip()
            product_exists = False
            if original_product_id:
                try:
                    info = wa.call("shop.product.getInfo", params={"id": original_product_id})
                    product_exists = bool(isinstance(info, dict) and str(info.get("id") or original_product_id))
                except Exception as exc:
                    text = str(exc or "").casefold()
                    if "http 404" not in text and "товар не найден" not in text:
                        raise

            if product_exists:
                desired = row["desired"]
                supplier_code = str(row.get("supplier_sku") or "").strip()
                temporary_sku = str(row.get("sku") or supplier_code).strip()
                sku_data = _sku_write_data(desired, rules, stock_id, stock_ids)
                sku_data.update({
                    "available": 1 if (desired.get("stock") or 0) > 0 else 0,
                    "status": 1,
                    "sku": temporary_sku,
                    "name": supplier_code,
                })
                created = wa.call(
                    "shop.product.skus.add",
                    http_method="POST",
                    params={"product_id": original_product_id},
                    data=sku_data,
                )
                sku_id = _extract_sku_id(created)
                if not sku_id:
                    skus = _listify(wa.call(
                        "shop.product.skus.getList",
                        params={"product_id": original_product_id},
                    ))
                    exact = [
                        item for item in skus
                        if str(item.get("name") or "").strip() == supplier_code
                        or str(item.get("sku") or "").strip() == temporary_sku
                    ]
                    if len(exact) != 1:
                        raise RuntimeError(
                            "После восстановления SKU %s найдено %d совпадений"
                            % (supplier_code, len(exact))
                        )
                    sku_id = str(exact[0].get("id") or "")
                if not sku_id:
                    raise RuntimeError("Webasyst не вернул ID восстановленного SKU %s" % supplier_code)
                wa.call(
                    "shop.product.skus.update",
                    http_method="POST",
                    params={"id": sku_id},
                    data={
                        "sku": sku_id if sku_mode == "numeric" else temporary_sku,
                        "name": supplier_code,
                    },
                )
                product_data = _product_write_data(desired, rules, web, creating=False)
                if product_data:
                    wa.call(
                        "shop.product.update",
                        http_method="POST",
                        params={"id": original_product_id},
                        data=product_data,
                    )
                result["recovered_skus"] += 1
                add_mapping(row, original_product_id, sku_id)
                return

            product_id, sku_id = create_full_product(row)
            result["recreated_products"] += 1
            add_mapping(row, product_id, sku_id)

        def apply_update_row(row, client):
            desired = row["desired"]
            current_product = row.get("current_product") or {}
            current_sku = row.get("current_sku") or {}
            sku_data = _sku_write_data(desired, rules, stock_id, stock_ids)

            supplier_code = str(row.get("supplier_sku") or "").strip()
            desired_sku = str(row.get("sku") or supplier_code).strip()
            current_code = str(current_sku.get("sku") or "").strip()
            numeric_code = str(row.get("sku_id") or "").strip()

            if supplier_code and str(current_sku.get("name") or "").strip() != supplier_code:
                sku_data["name"] = supplier_code

            if sku_mode == "numeric":
                if current_code == desired_sku and numeric_code.isdigit():
                    sku_data["sku"] = numeric_code
            else:
                if current_code == numeric_code and desired_sku:
                    sku_data["sku"] = desired_sku

            sku_data = _delta_sku_data(sku_data, current_sku, stock_id)
            sku_changed = bool(sku_data)
            if sku_data:
                try:
                    client.call(
                        "shop.product.skus.update",
                        http_method="POST",
                        params={"id": str(row["sku_id"])},
                        data=sku_data,
                    )
                except Exception as exc:
                    if _is_missing_webasyst_sku_error(exc):
                        return {"missing_sku": True}
                    raise

            product_data = _product_write_data(desired, rules, web, creating=False)
            if (
                str(current_product.get("status") or "0") == "0"
                and str(current_sku.get("sku") or "").strip() == str(row.get("supplier_sku") or "").strip()
            ):
                product_data["status"] = 1
            product_data = _delta_product_data(product_data, current_product)
            product_changed = bool(product_data)
            if product_data:
                client.call(
                    "shop.product.update",
                    http_method="POST",
                    params={"id": str(row["product_id"])},
                    data=product_data,
                )
            return {
                "missing_sku": False,
                "unchanged": not sku_changed and not product_changed,
            }

        missing_updates = []
        update_rows = list(plan["update"])
        use_parallel_updates = isinstance(wa, WebasystClient) and len(update_rows) > 1
        if use_parallel_updates:
            def run_update(row):
                return apply_update_row(row, WebasystClient())

            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                future_map = {pool.submit(run_update, row): row for row in update_rows}
                done = 0
                for future in concurrent.futures.as_completed(future_map):
                    row = future_map[future]
                    outcome = future.result()
                    done += 1
                    if outcome.get("missing_sku"):
                        missing_updates.append(row)
                    else:
                        if outcome.get("unchanged"):
                            result["unchanged_updates"] += 1
                        result["updated"] += 1
                        add_mapping(row, row["product_id"], row["sku_id"])
                    if done % 250 == 0 or done == len(update_rows):
                        print("Webasyst delta sync: %d/%d products" % (done, len(update_rows)), flush=True)
        else:
            for row in update_rows:
                outcome = apply_update_row(row, wa)
                if outcome.get("missing_sku"):
                    missing_updates.append(row)
                else:
                    if outcome.get("unchanged"):
                        result["unchanged_updates"] += 1
                    result["updated"] += 1
                    add_mapping(row, row["product_id"], row["sku_id"])

        # Missing/deleted SKU recovery remains serialized. It re-scans the
        # current catalog before any create/relink action and must not race.
        for row in missing_updates:
            restore_missing_sku(row)

        for row in plan.get("repair_sku", []):
            restore_missing_sku(row)

        for row in plan["create"]:
            product_id, sku_id = create_full_product(row)
            add_mapping(row, product_id, sku_id)

        if rules.get("zero_if_missing", False) and rules.get("update_stock", True):
            for row in plan["zero"]:
                try:
                    wa.call(
                        "shop.product.skus.update",
                        http_method="POST",
                        params={"id": str(row["sku_id"])},
                        data={
                            "stock": (
                                {str(int(x)): "0" for x in stock_ids if str(x).isdigit() and int(x) > 0}
                                if rules.get("zero_other_stocks", False)
                                else {str(int(stock_id)): "0"}
                            ),
                            "available": 0,
                        },
                    )
                except Exception as exc:
                    if not _is_missing_webasyst_sku_error(exc):
                        raise
                    result["stale_zero_links"] += 1
                    if len(result["stale_zero_sample"]) < 100:
                        result["stale_zero_sample"].append({
                            "supplier_sku": row.get("supplier_sku"),
                            "product_id": row.get("product_id"),
                            "sku_id": row.get("sku_id"),
                            "stage": "zero",
                        })
                    continue
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
