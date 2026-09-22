#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import mimetypes
import os
import re
import tempfile
import time
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import requests

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / "sync_aletan_kit.py"
REPORT_PATH = HERE / "last_catalog_sync_report.json"
BRAND = "Алетан"
ROOT_CATEGORY = "Алетан"
WAREHOUSE_NAMES = ("МСК", "СПБ привозной")
STOCK_QTY = 100


def load_base():
    spec = importlib.util.spec_from_file_location("aletan_price_base", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {BASE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


base = load_base()


def s(v):
    return str(v or "").strip()


def norm(v):
    return " ".join(s(v).casefold().replace("ё", "е").split())


def split_pictures(value):
    return [x.strip() for x in s(value).split(";") if x.strip()]


def load_catalog_full():
    r = requests.get(
        base.CATALOG_URL,
        timeout=180,
        headers={"User-Agent": "Mozilla/5.0 Aletan-KIT-Catalog-Sync"},
    )
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(base.decode_csv(r.content)), delimiter=";"))
    by_code = {}
    duplicates = {}
    for row in rows:
        if norm(row.get("vendor")) != norm(BRAND):
            continue
        code = base.clean_code(row.get("vendorCode") or row.get("id"))
        if not code:
            continue
        item = {
            "vendor_code": code,
            "name": s(row.get("name")) or code,
            "catalog_price": base.money(row.get("price")),
            "category": s(row.get("category")) or ROOT_CATEGORY,
            "description": s(row.get("description")),
            "url": s(row.get("url")),
            "pictures": split_pictures(row.get("picture")),
            "collection": s(row.get("collection")),
            "material": s(row.get("material")),
            "color": s(row.get("color")),
            "dimensions": s(row.get("dimensions")),
            "width": s(row.get("width")),
            "height": s(row.get("height")),
            "depth": s(row.get("depth")),
            "weight": s(row.get("weight")),
            "country": s(row.get("country_of_origin")),
        }
        if code in by_code:
            duplicates.setdefault(code, [by_code[code]]).append(item)
        else:
            by_code[code] = item
    for code in duplicates:
        by_code.pop(code, None)
    return by_code, duplicates, len(rows)


class KitExt(base.KitClient):
    def create_category(self, title, parent_id=None):
        body = {"title": title}
        if parent_id:
            body["parent_id"] = parent_id
        return self.request("POST", "/v1/categories", body=body)

    def create_characteristic(self, title):
        return self.request(
            "POST",
            "/v1/characteristics",
            body={"title": title, "type": "STRING", "select_mode": "SINGLE"},
        )

    def create_product(self, category_id):
        return self.request("POST", "/v1/products", body={"category_ids": [category_id]})

    def create_variant(self, body):
        return self.request("POST", "/v1/variants", body=body, timeout=150)

    def patch_variant(self, variant_id, body):
        return self.request("PATCH", f"/v1/variants/{variant_id}", body=body, timeout=120)

    def update_stocks(self, items):
        for start in range(0, len(items), 5000):
            self.request(
                "POST",
                "/v1/variants/stocks/bulk_update",
                body={"items": items[start:start + 5000]},
                timeout=180,
            )

    def upload_image_url(self, url):
        delay = 0.55 - (time.monotonic() - self.last_request_at)
        if delay > 0:
            time.sleep(delay)
        self.last_request_at = time.monotonic()
        source = requests.get(
            url,
            timeout=120,
            headers={"User-Agent": "Mozilla/5.0 Aletan-KIT-Catalog-Sync"},
        )
        source.raise_for_status()
        content_type = (
            source.headers.get("Content-Type")
            or mimetypes.guess_type(urlparse(url).path)[0]
            or "image/jpeg"
        )
        ext = os.path.splitext(urlparse(url).path)[1] or ".jpg"
        endpoint = base.KIT_API + "/v1/files"
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        r = self.session.post(
            endpoint,
            headers=headers,
            files={"file": ("aletan" + ext[:10], source.content, content_type)},
            timeout=150,
        )
        r.raise_for_status()
        return r.json() if r.content else {}


def exact_named(rows, title, *, parent_id=None):
    matches = [
        row for row in rows
        if norm(row.get("title")) == norm(title)
        and (parent_id is None or s(row.get("parent_id")) == s(parent_id))
    ]
    return matches[0] if matches else None


def characteristic_row(cid, value):
    return {"characteristic_id": cid, "value": value}


def merge_characteristic(existing, cid, value):
    rows = list(existing or [])
    found = False
    for row in rows:
        if s(row.get("characteristic_id")) == s(cid):
            row["value"] = value
            row.pop("values", None)
            found = True
            break
    if not found:
        rows.append(characteristic_row(cid, value))
    return rows


def prepare_media(kit, item, report):
    media = []
    for url in item["pictures"][:12]:
        try:
            uploaded = kit.upload_image_url(url)
            file_id = s(uploaded.get("id"))
            if file_id:
                media.append({
                    "type": "IMAGE",
                    "display_sequence": len(media),
                    "image_id": file_id,
                })
                report["images_uploaded"] += 1
        except Exception as exc:
            report["image_errors"] += 1
            if len(report["warnings"]) < 100:
                report["warnings"].append({
                    "vendor_code": item["vendor_code"],
                    "stage": "image",
                    "url": url,
                    "message": str(exc)[:500],
                })
    return media


def run(dry_run=False, force=False):
    report = {
        "status": "running",
        "dry_run": bool(dry_run),
        "brand": BRAND,
        "creation_rule": "создавать отсутствующие товары с SKU=vendorCode",
        "category_rule": "Алетан/<category из CSV>",
        "price_rule": {
            "Цена для покупателя": "закупка × 1.30, вверх до 1 ₽",
            "Цена до скидки": "закупка × 1.60, вверх до 1 ₽",
        },
        "stock_rule": {
            "МСК": STOCK_QTY,
            "СПБ привозной": STOCK_QTY,
        },
        "created": 0,
        "would_create": 0,
        "existing_matched": 0,
        "price_updates": 0,
        "stock_updates": 0,
        "skipped_no_purchase_price": 0,
        "skipped_ambiguous_kit": 0,
        "images_uploaded": 0,
        "image_errors": 0,
        "errors": [],
        "warnings": [],
        "sample_new": [],
    }

    if not dry_run:
        due, age = base.due_for_live(force)
        report["days_since_last_success"] = None if age is None else round(age, 2)
        if not due:
            report["status"] = "skipped"
            report["reason"] = "С последнего успешного live-обновления прошло менее 14 дней"
            REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0

    token = s(os.getenv("YANDEX_KIT_TOKEN"))
    if not token:
        raise RuntimeError("YANDEX_KIT_TOKEN is not configured")

    catalog, duplicates, raw_count = load_catalog_full()
    report["catalog_rows_total"] = raw_count
    report["catalog_unique_vendor_codes"] = len(catalog)
    report["catalog_duplicate_vendor_codes"] = sorted(duplicates)[:100]

    price_catalog = {
        code: {"catalog_price": item["catalog_price"], "name": item["name"]}
        for code, item in catalog.items()
    }
    with tempfile.TemporaryDirectory(prefix="aletan-full-") as td:
        price_file = Path(td) / "aletan-price.xlsx"
        base.download_price_xlsx(price_file)
        detected = base.detect_price_map(price_file, price_catalog)

    purchase_prices = detected["prices"]
    report["purchase_prices_unique"] = len(purchase_prices)
    report["price_sheets_used"] = detected.get("sheets_used", [])
    report["purchase_price_conflicts"] = detected.get("conflicts", {})

    kit = KitExt(token)

    warehouses = kit.list_all("/v1/warehouses", {"status": "ACTIVE"}, "warehouses")
    warehouse_ids = {s(x.get("title")): s(x.get("id")) for x in warehouses if s(x.get("id"))}
    missing_wh = [name for name in WAREHOUSE_NAMES if not warehouse_ids.get(name)]
    if missing_wh:
        raise RuntimeError("Не найдены склады KIT: " + ", ".join(missing_wh))
    report["kit_warehouses"] = {name: warehouse_ids[name] for name in WAREHOUSE_NAMES}

    source_codes = set(catalog)
    index, kit_ambiguous, samples, branded_count, detailed_reads = base.build_kit_index(kit, source_codes)
    report["kit_aletan_active_variants_before"] = branded_count
    report["kit_detailed_variant_reads"] = detailed_reads
    report["kit_ambiguous_vendor_codes"] = sorted(kit_ambiguous)[:100]

    # Остатки 100/100 применяются ко ВСЕМ активным карточкам бренда Алетан,
    # даже если в текущем прайсе нет закупочной цены.
    all_brand_rows = kit.list_all("/v1/variants", {"name": BRAND}, "variants")
    all_brand_active = [
        row for row in all_brand_rows
        if norm(row.get("brand")) == norm(BRAND)
        and s(row.get("status")).upper() != "ARCHIVED"
        and s(row.get("id"))
    ]
    unique_brand_variants = {}
    for row in all_brand_active:
        unique_brand_variants[s(row.get("id"))] = row
    report["kit_stock_variants_targeted"] = len(unique_brand_variants)

    characteristics = kit.list_all("/v1/characteristics", {"status": "ACTIVE"}, "characteristics")
    article_char = exact_named(characteristics, "Артикул поставщика")
    if article_char is None and not dry_run:
        article_char = kit.create_characteristic("Артикул поставщика")
        characteristics.append(article_char)
    article_char_id = s((article_char or {}).get("id"))
    if not dry_run and not article_char_id:
        raise RuntimeError("Не удалось определить характеристику «Артикул поставщика»")
    report["supplier_article_characteristic_id"] = article_char_id or "WOULD_CREATE"

    categories = kit.list_all("/v1/categories", {"status": "ACTIVE"}, "categories")
    root = exact_named(categories, ROOT_CATEGORY, parent_id="")
    if root is None:
        if dry_run:
            root_id = "DRY_RUN_ROOT"
        else:
            root = kit.create_category(ROOT_CATEGORY)
            root_id = s(root.get("id"))
            categories.append(dict(root, parent_id=""))
    else:
        root_id = s(root.get("id"))
    if not root_id:
        raise RuntimeError("Не удалось определить корневую категорию Алетан")

    child_cache = {}
    def get_category_id(title):
        title = title or ROOT_CATEGORY
        if title in child_cache:
            return child_cache[title]
        if norm(title) == norm(ROOT_CATEGORY):
            child_cache[title] = root_id
            return root_id
        row = exact_named(categories, title, parent_id=root_id)
        if row:
            cid = s(row.get("id"))
        elif dry_run:
            cid = "DRY_RUN_" + re.sub(r"\W+", "_", title)[:60]
        else:
            row = kit.create_category(title, root_id)
            cid = s(row.get("id"))
            categories.append(dict(row, parent_id=root_id))
        if not cid:
            raise RuntimeError(f"Не удалось создать категорию {title!r}")
        child_cache[title] = cid
        return cid

    price_batch = []
    stock_batch = [
        {
            "variant_id": variant_id,
            "warehouse_id": warehouse_ids[wh],
            "quantity": STOCK_QTY,
        }
        for variant_id in unique_brand_variants
        for wh in WAREHOUSE_NAMES
    ]
    report["stock_updates"] = len(stock_batch)

    for code, item in catalog.items():
        if code in kit_ambiguous:
            report["skipped_ambiguous_kit"] += 1
            continue

        purchase = purchase_prices.get(code)
        variant = index.get(code)
        sale = old = None
        if purchase is not None:
            sale = base.ruble_up(purchase * Decimal("1.30"))
            old = base.ruble_up(purchase * Decimal("1.60"))

        if variant:
            report["existing_matched"] += 1
            vid = s(variant.get("id"))
            if purchase is not None:
                price_batch.append({
                    "variant_id": vid,
                    "price": str(old),
                    "manual_discount_price": str(sale),
                })
                report["price_updates"] += 1

            # Add supplier article characteristic where possible, but never rename SKU.
            if not dry_run and article_char_id:
                try:
                    detail = kit.get_variant(vid)
                    merged = merge_characteristic(detail.get("characteristics"), article_char_id, code)
                    if merged != list(detail.get("characteristics") or []):
                        kit.patch_variant(vid, {"characteristics": merged})
                except Exception as exc:
                    if len(report["warnings"]) < 100:
                        report["warnings"].append({
                            "vendor_code": code,
                            "stage": "existing_characteristic",
                            "message": str(exc)[:500],
                        })
            continue

        if purchase is None:
            report["skipped_no_purchase_price"] += 1
            if len(report["warnings"]) < 100:
                report["warnings"].append({
                    "vendor_code": code,
                    "stage": "create",
                    "message": "Новый товар не создан: в прайсе нет надежной закупочной цены",
                })
            continue

        report["would_create"] += 1
        if len(report["sample_new"]) < 30:
            report["sample_new"].append({
                "vendor_code": code,
                "sku": code,
                "name": item["name"],
                "category": item["category"],
                "purchase": str(base.q2(purchase)),
                "customer_price": str(sale),
                "old_price": str(old),
                "pictures": len(item["pictures"]),
            })
        if dry_run:
            continue

        category_id = get_category_id(item["category"])
        product = kit.create_product(category_id)
        product_id = s(product.get("id"))
        if not product_id:
            raise RuntimeError(f"{code}: KIT не вернул product_id")

        media = prepare_media(kit, item, report)
        characteristics_payload = [characteristic_row(article_char_id, code)] if article_char_id else []
        body = {
            "sku": code,
            "name": item["name"],
            "description": item["description"],
            "status": "PUBLISHED",
            "product_id": product_id,
            "brand": BRAND,
            "stocks": [
                {
                    "warehouse_id": warehouse_ids[wh],
                    "quantity": STOCK_QTY,
                    "reserved": 0,
                }
                for wh in WAREHOUSE_NAMES
            ],
            "pricing": {
                "price": str(old),
                "manual_discount_price": str(sale),
            },
            "characteristics": characteristics_payload,
        }
        if media:
            body["media"] = media

        created = kit.create_variant(body)
        if not s(created.get("id")):
            raise RuntimeError(f"{code}: KIT не вернул variant_id")
        report["created"] += 1

    if not dry_run:
        if price_batch:
            kit.update_prices(price_batch)
        if stock_batch:
            kit.update_stocks(stock_batch)
        base.save_state_success()

    report["status"] = "ok"
    report["complete"] = not report["errors"]
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    try:
        return run(args.dry_run, args.force)
    except Exception as exc:
        report = {"status": "error", "dry_run": bool(args.dry_run), "error": str(exc)[:3000]}
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
