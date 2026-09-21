#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PRODUCTS_PATH = HERE / "output" / "products.json"
MAPPING_PATH = HERE / "kit_mapping.json"
REPORT_PATH = HERE / "last_kit_sync_report.json"
STATE_PATH = HERE / "last_schedule_state.json"

BRAND = "Levmar"
WEBASYST_VALUE = "337"
SKU_PREFIX = "337-"
ROOT_CATEGORY = "Levmar"
MONEY = Decimal("0.01")

# Hard rule: supplier wholesale/purchase price is NEVER sent to KIT.
PRICE_KEY_RE = re.compile(r"(?:цена|опт|закуп|рознич|ррц|price|cost)", re.I)


def s(v):
    return str(v or "").strip()


def norm(v):
    return re.sub(r"[^0-9a-zа-яё]+", "", unicodedata.normalize("NFKC", s(v)).casefold())


def dec(v):
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None


def money(v):
    d = dec(v)
    return None if d is None else d.quantize(MONEY, rounding=ROUND_HALF_UP)


def pricing_from_retail(retail):
    retail = money(retail)
    if retail is None or retail <= 0:
        return None
    customer = money(retail * Decimal("0.95"))
    before_discount = money(retail * Decimal("1.30"))
    return {
        "retail_source": retail,
        "customer": customer,
        "before_discount": before_discount,
    }


def load_kit_module():
    path = ROOT / "kenner-kit" / "sync_kenner_kit.py"
    spec = importlib.util.spec_from_file_location("kenner_kit_for_stol337", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_products():
    if not PRODUCTS_PATH.exists():
        raise RuntimeError(f"Parser output not found: {PRODUCTS_PATH}")
    raw = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))
    rows = raw.get("products") or []
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Parser output contains no products")

    out = {}
    duplicates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        supplier_article = s(row.get("sku"))
        if not supplier_article:
            continue
        key = supplier_article.casefold()
        if key in out:
            duplicates.append(supplier_article)
            continue

        chars = {}
        for title, value in (row.get("characteristics") or {}).items():
            title = s(title)
            value = s(value)
            if not title or not value or PRICE_KEY_RE.search(title):
                continue
            chars[title] = value

        # Important structured fields are also written as ordinary characteristics
        # when the supplier table did not already provide them.
        for title, value in (
            ("Модель", row.get("model")),
            ("Цвет", row.get("color")),
            ("Цвет опор", row.get("support_color")),
        ):
            if s(value) and not any(norm(k) == norm(title) for k in chars):
                chars[title] = s(value)

        images = []
        for url in row.get("images") or []:
            url = s(url)
            if not url or "/swatches/" in url.lower():
                continue
            if url not in images:
                images.append(url)

        out[key] = {
            "supplier_article": supplier_article,
            "name": s(row.get("name")) or supplier_article,
            "description": s(row.get("description")),
            "retail_price": s(row.get("retail_price")),
            # purchase_price deliberately ignored.
            "source_url": s(row.get("url")),
            "pictures": images,
            "characteristics": chars,
        }
    return out, duplicates


def load_mapping():
    if not MAPPING_PATH.exists():
        return {"variants": {}}
    try:
        data = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("variants", {})
            return data
    except Exception:
        pass
    return {"variants": {}}


def save_mapping(mapping):
    MAPPING_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")


def image_hash(urls):
    payload = "\n".join(urls).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def char_value(variant, cid):
    for row in variant.get("characteristics") or []:
        if s(row.get("characteristic_id")) != s(cid):
            continue
        values = row.get("values") or []
        return s(row.get("value")) or (s(values[0]) if values else "")
    return ""


def run():
    token = s(os.getenv("YANDEX_KIT_TOKEN"))
    if not token:
        raise RuntimeError("YANDEX_KIT_TOKEN is not configured")

    offers, source_duplicates = load_products()
    kit_mod = load_kit_module()
    kit = kit_mod.KitClient(token)

    report = {
        "status": "running",
        "brand": BRAND,
        "webasyst_characteristic": WEBASYST_VALUE,
        "source_products": len(offers),
        "source_duplicate_articles": source_duplicates[:100],
        "sku_rule": "337-<KIT kit_id>",
        "price_rules": {
            "customer": "supplier retail * 0.95",
            "before_discount": "supplier retail * 1.30",
            "purchase": "NOT SENT TO KIT",
        },
        "stock_rule": "source has no stock field; KIT stock is not modified",
        "created": 0,
        "updated": 0,
        "price_updates": 0,
        "images_uploaded": 0,
        "image_errors": 0,
        "characteristics_updated": 0,
        "missing_retail_price": [],
        "mapped_products_missing_from_source": [],
        "warnings": [],
        "errors": [],
        "complete": False,
    }

    categories = kit.list_all("/v1/categories", {"status": "ACTIVE"}, "categories")

    def ensure_category(title, parent_id=""):
        matches = [
            x for x in categories
            if norm(x.get("title")) == norm(title)
            and s(x.get("parent_id")) == s(parent_id)
        ]
        if matches:
            return s(matches[0].get("id"))
        created = kit.create_category(title, parent_id or None)
        cid = s(created.get("id"))
        if not cid:
            raise RuntimeError(f"KIT did not return category id for {title!r}")
        row = dict(created)
        row.setdefault("parent_id", parent_id)
        categories.append(row)
        return cid

    root_category_id = ensure_category(ROOT_CATEGORY)

    characteristics = kit.list_all("/v1/characteristics", {"status": "ACTIVE"}, "characteristics")
    char_cache = {}

    def ensure_char(title):
        key = norm(title)
        if key in char_cache:
            return char_cache[key]
        matches = [x for x in characteristics if norm(x.get("title")) == key]
        if matches:
            string_matches = [
                x for x in matches
                if s(x.get("type")).upper() in ("STRING", "MULTIPLE_STRING")
            ]
            cid = s((string_matches or matches)[0].get("id"))
        else:
            created = kit.create_characteristic(title)
            cid = s(created.get("id"))
            if not cid:
                raise RuntimeError(f"KIT did not return characteristic id for {title!r}")
            characteristics.append(created)
        char_cache[key] = cid
        return cid

    webasyst_cid = ensure_char("Webasyst")
    supplier_cid = ensure_char("Артикул поставщика")
    article_cid = ensure_char("Артикул")
    site_code_cid = ensure_char("Код для сайта")

    # Explicitly detect and exclude any purchase-price characteristic if one
    # already exists globally in KIT.
    purchase_char_ids = {
        s(x.get("id"))
        for x in characteristics
        if re.search(r"(?:закупочн|purchase|cost)", s(x.get("title")), re.I)
    }

    mapping = load_mapping()
    mapping.setdefault("variants", {})

    # Recover state if mapping file is ever lost.
    if not mapping["variants"]:
        try:
            for row in kit.list_all("/v1/variants", {"name": SKU_PREFIX}, "variants"):
                if s(row.get("status")).upper() == "ARCHIVED":
                    continue
                vid = s(row.get("id"))
                if not vid:
                    continue
                full = kit.get_variant(vid)
                if char_value(full, webasyst_cid) != WEBASYST_VALUE:
                    continue
                supplier = char_value(full, supplier_cid)
                if supplier:
                    mapping["variants"][supplier.casefold()] = {
                        "variant_id": vid,
                        "kit_id": full.get("kit_id"),
                        "sku": s(full.get("sku")),
                        "image_hash": "",
                    }
            save_mapping(mapping)
        except Exception as exc:
            report["warnings"].append("Mapping rebuild warning: " + str(exc)[:500])

    def make_char_row(title, value, cid=None):
        value = s(value)
        if not value:
            return None
        cid = cid or ensure_char(title)
        return {"characteristic_id": cid, "value": value, "values": [value]}

    def source_chars(item):
        rows = [
            make_char_row("Webasyst", WEBASYST_VALUE, webasyst_cid),
            make_char_row("Артикул поставщика", item["supplier_article"], supplier_cid),
        ]
        used = {norm("Webasyst"), norm("Артикул поставщика")}
        for title, value in item.get("characteristics", {}).items():
            if PRICE_KEY_RE.search(title) or norm(title) in used:
                continue
            row = make_char_row(title, value)
            if row:
                rows.append(row)
                used.add(norm(title))
        return [x for x in rows if x]

    def final_chars(item, final_sku):
        rows = source_chars(item)
        rows.extend([
            make_char_row("Артикул", final_sku, article_cid),
            make_char_row("Код для сайта", final_sku, site_code_cid),
        ])
        return [x for x in rows if x]

    def prepare_media(item):
        media = []
        for url in item["pictures"]:
            try:
                uploaded = kit.upload_image_url(url)
                fid = s(uploaded.get("id"))
                if fid:
                    media.append({
                        "type": "IMAGE",
                        "display_sequence": len(media),
                        "image_id": fid,
                    })
                    report["images_uploaded"] += 1
            except Exception as exc:
                report["image_errors"] += 1
                if len(report["warnings"]) < 300:
                    report["warnings"].append(
                        f"{item['supplier_article']}: image failed {url}: {str(exc)[:300]}"
                    )
        return media

    price_rows = []

    for key, item in offers.items():
        try:
            ps = pricing_from_retail(item.get("retail_price"))
            if ps is None:
                report["missing_retail_price"].append(item["supplier_article"])

            mapped = mapping["variants"].get(key)
            current_image_hash = image_hash(item["pictures"])

            if mapped:
                variant_id = s(mapped.get("variant_id"))
                variant = kit.get_variant(variant_id)
                kit_id = variant.get("kit_id") or mapped.get("kit_id")
                if kit_id in (None, ""):
                    raise RuntimeError("Existing variant has no kit_id")
                final_sku = f"{SKU_PREFIX}{kit_id}"

                existing = [
                    x for x in (variant.get("characteristics") or [])
                    if s(x.get("characteristic_id")) not in purchase_char_ids
                ]
                by_id = {
                    s(x.get("characteristic_id")): x
                    for x in existing
                    if s(x.get("characteristic_id"))
                }
                for row in final_chars(item, final_sku):
                    by_id[s(row["characteristic_id"])] = row

                patch = {
                    "sku": final_sku,
                    "name": item["name"],
                    "brand": BRAND,
                    "characteristics": list(by_id.values()),
                }
                if item["description"]:
                    patch["description"] = item["description"]

                if s(mapped.get("image_hash")) != current_image_hash and item["pictures"]:
                    media = prepare_media(item)
                    if media:
                        patch["media"] = media
                        mapped["image_hash"] = current_image_hash

                kit.patch_variant(variant_id, patch)
                report["updated"] += 1
                report["characteristics_updated"] += 1
            else:
                product = kit.create_product(root_category_id)
                product_id = s(product.get("id"))
                if not product_id:
                    raise RuntimeError("KIT did not return product id")

                safe = re.sub(r"[^0-9A-Za-zА-Яа-я]+", "-", item["supplier_article"])[:40].strip("-") or "ITEM"
                tmp_sku = f"LEV337-TMP-{safe}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]}"

                body = {
                    "sku": tmp_sku,
                    "name": item["name"],
                    "description": item["description"],
                    "status": "PUBLISHED",
                    "product_id": product_id,
                    "brand": BRAND,
                    "characteristics": source_chars(item),
                }
                if ps:
                    body["pricing"] = {
                        "price": f"{ps['before_discount']:.2f}",
                        "manual_discount_price": f"{ps['customer']:.2f}",
                    }

                media = prepare_media(item)
                if media:
                    body["media"] = media

                created = kit.create_variant(body)
                variant_id = s(created.get("id"))
                if not variant_id:
                    raise RuntimeError("KIT did not return variant id")

                kit_id = created.get("kit_id")
                if kit_id in (None, ""):
                    created = kit.get_variant(variant_id)
                    kit_id = created.get("kit_id")
                if kit_id in (None, ""):
                    raise RuntimeError("KIT did not return kit_id")

                final_sku = f"{SKU_PREFIX}{kit_id}"
                mapping["variants"][key] = {
                    "variant_id": variant_id,
                    "kit_id": kit_id,
                    "sku": final_sku,
                    "image_hash": current_image_hash if media else "",
                }
                # Persist immediately to make interrupted imports idempotent.
                save_mapping(mapping)

                kit.patch_variant(variant_id, {
                    "sku": final_sku,
                    "name": item["name"],
                    "brand": BRAND,
                    "characteristics": final_chars(item, final_sku),
                })
                report["created"] += 1
                report["characteristics_updated"] += 1

            variant_id = s(mapping["variants"][key]["variant_id"])
            mapping["variants"][key]["sku"] = final_sku
            mapping["variants"][key]["kit_id"] = kit_id
            if ps:
                price_rows.append({
                    "variant_id": variant_id,
                    "price": f"{ps['before_discount']:.2f}",
                    "manual_discount_price": f"{ps['customer']:.2f}",
                })
        except Exception as exc:
            report["errors"].append({
                "article": item["supplier_article"],
                "message": str(exc)[:1000],
            })

    for key, mapped in mapping["variants"].items():
        if key not in offers:
            report["mapped_products_missing_from_source"].append(s(mapped.get("sku")) or key)

    for start in range(0, len(price_rows), 500):
        batch = price_rows[start:start + 500]
        kit.request(
            "POST",
            "/v1/variants/prices/bulk_update",
            body={"items": batch},
        )
        report["price_updates"] += len(batch)

    save_mapping(mapping)
    report["mapped_products"] = len(mapping["variants"])
    report["status"] = "УСПЕШНО" if not report["errors"] else "ЗАВЕРШЕНО С ОШИБКАМИ"
    report["complete"] = (
        not report["errors"]
        and len(mapping["variants"]) >= len(offers)
        and not report["missing_retail_price"]
    )
    report["generated_at"] = datetime.now(timezone.utc).isoformat()

    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if report["complete"]:
        STATE_PATH.write_text(
            json.dumps(
                {
                    "last_success_at": report["generated_at"],
                    "products": len(offers),
                    "mapped_products": len(mapping["variants"]),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(run())
