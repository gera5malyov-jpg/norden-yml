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
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urljoin

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "webasyst"))

from client import WebasystClient

TYPE_NAME = "NORDEN-100"
BRAND = "Norden"
REPORT = HERE / "webasyst_bootstrap_report.json"
EXCLUDED_FEATURE_TITLES = {
    "ндс", "поставщик", "остаток", "остатки", "склад", "наличие",
}


def s(v):
    return str(v or "").strip()


def norm(v):
    return re.sub(r"[^0-9a-zа-яё]+", "", unicodedata.normalize("NFKC", s(v)).casefold())


def dec(v):
    try:
        return Decimal(str(v).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None


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


def feature_value(v):
    if v in (None, ""):
        return ""
    if isinstance(v, dict):
        for key in ("value", "name", "title"):
            if s(v.get(key)):
                return s(v.get(key))
        vals = [feature_value(x) for x in v.values()]
        return " | ".join(dict.fromkeys(x for x in vals if x))
    if isinstance(v, (list, tuple)):
        vals = [feature_value(x) for x in v]
        return " | ".join(dict.fromkeys(x for x in vals if x))
    return s(v)


def load_norden_module():
    path = HERE / "sync_norden_kit.py"
    spec = importlib.util.spec_from_file_location("norden_sync", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
        total = (payload.get("count") or payload.get("total_count")) if isinstance(payload, dict) else None
        out.extend(batch)
        if not batch or len(batch) < 1000:
            break
        if total not in (None, "") and len(out) >= int(total):
            break
        offset += len(batch)
    return out


def main():
    mod = load_norden_module()
    kit = mod.KitClient(os.environ.get("YANDEX_KIT_TOKEN", ""))
    wa = WebasystClient(min_request_interval=0.60)
    base_url = (os.environ.get("WEBASYST_BASE_URL") or "https://profikompany.ru").rstrip("/") + "/"

    report = {
        "started_at": now_iso(),
        "type_name": TYPE_NAME,
        "type_id": None,
        "webasyst_products": 0,
        "webasyst_skus": 0,
        "matched_exact_sku": 0,
        "matched_by_norden_code": 0,
        "archived_exact_sku": 0,
        "moscow_only_skipped": 0,
        "missing_before": 0,
        "created_in_kit": 0,
        "created_without_source_match": 0,
        "images_uploaded": 0,
        "image_errors": 0,
        "characteristics_written": 0,
        "missing_price_skipped": 0,
        "errors": [],
        "warnings": [],
        "created": [],
        "skipped": [],
        "complete": False,
    }

    types = listify(wa.call("shop.type.getList"))
    type_matches = [x for x in types if norm(x.get("name") or x.get("title")) == norm(TYPE_NAME)]
    if len(type_matches) != 1:
        raise RuntimeError(f"Expected exactly one Webasyst type {TYPE_NAME!r}, found {len(type_matches)}")
    type_id = s(type_matches[0].get("id"))
    report["type_id"] = type_id

    products = load_wa_products(wa, type_id)
    report["webasyst_products"] = len(products)

    feature_defs = listify(wa.call("shop.feature.getList"), ("features", "items"))
    feature_title_by_code = {
        s(x.get("code")): s(x.get("name") or x.get("title"))
        for x in feature_defs if s(x.get("code"))
    }

    secret = os.environ.get("NORDEN_SECRET", "").strip()
    source, _, source_kind, api_error = mod.load_source(secret, short=False)
    if len(source) < 1000:
        raise RuntimeError(f"Safety stop: unexpectedly small Norden source ({len(source)})")
    source_articles = list(source)
    report["source"] = source_kind
    report["api_error"] = api_error

    warehouses = mod.resolve_warehouses(kit)
    categories = kit.categories()
    all_chars, chars_by_title, code_site_id, article_id = mod.resolve_special_characteristics(kit)
    mapping = mod.load_mapping()

    def extract_features(info):
        raw = info.get("features") or {} if isinstance(info, dict) else {}
        out = []
        code_for_site = ""
        if isinstance(raw, dict):
            for code, raw_value in raw.items():
                title = feature_title_by_code.get(s(code), s(code))
                value = feature_value(raw_value)
                if not title or not value:
                    continue
                if norm(title) in {norm("Код для сайта"), norm("Код Norden")} and not code_for_site:
                    code_for_site = value
                if norm(title) in {norm("Артикул"), norm("Код для сайта"), norm("Код Norden")}:
                    continue
                if norm(title) in {norm(x) for x in EXCLUDED_FEATURE_TITLES}:
                    continue
                out.append((title, value))
        return code_for_site, out

    def exact_kit_rows(sku):
        payload = kit.request("GET", "/v1/variants", params={"name": sku, "page": 1, "per_page": 100})
        return [
            x for x in kit.items(payload)
            if s(x.get("sku")) == sku and s(x.get("brand")).casefold() == BRAND.casefold()
        ]

    def active_from_mapping(article):
        rows = (mapping.get("variants") or {}).get(article) or []
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
        return active

    def image_urls(info):
        result = []
        images = info.get("images") or [] if isinstance(info, dict) else []
        if isinstance(images, dict):
            images = list(images.values())
        for row in images[:20] if isinstance(images, list) else []:
            if not isinstance(row, dict):
                continue
            image_id = s(row.get("id"))
            url = s(row.get("url_big") or row.get("url") or row.get("url_thumb"))
            if image_id:
                try:
                    detail = wa.call("shop.product.images.getInfo", params={"id": image_id, "size": "970"})
                    if isinstance(detail, dict):
                        url = s(detail.get("url_big") or detail.get("url_thumb") or url)
                except Exception as exc:
                    if len(report["warnings"]) < 200:
                        report["warnings"].append(f"image {image_id}: Webasyst getInfo failed: {exc}")
            if url:
                result.append(urljoin(base_url, url))
        return list(dict.fromkeys(result))

    for product in products:
        product_id = s(product.get("id"))
        sku_rows = product_skus(product)
        for sku_row in sku_rows:
            sku = s(sku_row.get("sku"))
            if not sku:
                continue
            report["webasyst_skus"] += 1
            try:
                exact = exact_kit_rows(sku)
                active_exact = [x for x in exact if s(x.get("status")).upper() != "ARCHIVED"]
                archived_exact = [x for x in exact if s(x.get("status")).upper() == "ARCHIVED"]

                if len(active_exact) == 1:
                    report["matched_exact_sku"] += 1
                    continue
                if len(active_exact) > 1:
                    raise RuntimeError(f"duplicate active KIT Norden SKU {sku}: {len(active_exact)}")

                info = wa.call("shop.product.getInfo", params={"id": product_id})
                code_for_site, wa_features = extract_features(info)
                source_article = mod.match_source_article(code_for_site, source_articles) if code_for_site else None
                if not source_article:
                    source_article = mod.match_source_article(sku, source_articles)
                item = source.get(source_article) if source_article else None

                name = s((info or {}).get("name") if isinstance(info, dict) else "") or s(product.get("name")) or sku
                name_fold = name.casefold()
                if ("только" in name_fold and ("москва" in name_fold or "москве" in name_fold)) or (
                    item and mod.is_moscow_only_product(item)
                ):
                    report["moscow_only_skipped"] += 1
                    report["skipped"].append({"sku": sku, "name": name, "reason": "только Москва"})
                    continue

                if archived_exact:
                    report["archived_exact_sku"] += 1
                    report["skipped"].append({"sku": sku, "name": name, "reason": "SKU already exists in KIT archive"})
                    continue

                if source_article:
                    by_code = active_from_mapping(source_article)
                    if len(by_code) == 1:
                        report["matched_by_norden_code"] += 1
                        continue
                    if len(by_code) > 1:
                        raise RuntimeError(f"multiple active KIT cards for Norden code {source_article}")

                report["missing_before"] += 1

                # User rule: never create a new Norden card in KIT unless
                # the supplier source confirms both a positive purchase price
                # and at least one supplier image.
                if not source_article or not item:
                    report["skipped"].append({
                        "sku": sku, "name": name,
                        "reason": "нет сопоставления с товаром Norden — нельзя проверить закупочную цену и фото",
                    })
                    continue
                purchase = dec(item.get("purchase"))
                if purchase is None or purchase <= 0:
                    report["missing_price_skipped"] += 1
                    report["skipped"].append({
                        "sku": sku, "name": name,
                        "reason": "нет положительной закупочной цены Norden",
                    })
                    continue
                if not (item.get("images") or []):
                    report.setdefault("missing_images_skipped", 0)
                    report["missing_images_skipped"] += 1
                    report["skipped"].append({
                        "sku": sku, "name": name,
                        "reason": "нет изображения Norden",
                    })
                    continue

                sale = dec(sku_row.get("price"))
                compare = dec(sku_row.get("compare_price"))
                kit_prices = mod.price_set(item.get("purchase"))
                if kit_prices:
                    old_price = Decimal(str(kit_prices["old"]))
                    sale_price = Decimal(str(kit_prices["sale"]))
                else:
                    sale_price = sale if sale and sale > 0 else None
                    old_price = compare if compare and compare > 0 else sale_price

                if old_price is None or old_price <= 0:
                    report["missing_price_skipped"] += 1
                    report["skipped"].append({"sku": sku, "name": name, "reason": "нет цены для публикации в KIT"})
                    continue

                category_path = (item.get("category_path") if item else None) or ["Norden"]
                category_id = mod.ensure_category_path(kit, categories, category_path)
                created_product = kit.create_product(category_id)
                kit_product_id = s(created_product.get("id"))
                if not kit_product_id:
                    raise RuntimeError("KIT did not return product id")

                body = {
                    "sku": sku,
                    "name": name,
                    "status": "PUBLISHED",
                    "product_id": kit_product_id,
                    "brand": BRAND,
                    "stocks": [
                        {"warehouse_id": wid, "quantity": 0, "reserved": 0}
                        for wid in warehouses.values()
                    ],
                    "pricing": {"price": str(old_price)},
                }
                if sale_price is not None and sale_price > 0:
                    body["pricing"]["manual_discount_price"] = str(sale_price)

                created_variant = kit.create_variant(body)
                variant_id = s(created_variant.get("id"))
                if not variant_id:
                    raise RuntimeError("KIT did not return variant id")
                full = kit.get_variant(variant_id)
                kit_id = full.get("kit_id")

                characteristics = []
                code_value = source_article or code_for_site
                if code_value:
                    characteristics.append({
                        "characteristic_id": code_site_id,
                        "value": code_value,
                        "values": [code_value],
                    })
                characteristics.append({
                    "characteristic_id": article_id,
                    "value": sku,
                    "values": [sku],
                })

                for title, value in wa_features:
                    try:
                        cid = mod.characteristic_id(kit, all_chars, chars_by_title, title)
                    except Exception as exc:
                        if len(report["warnings"]) < 200:
                            report["warnings"].append(f"{sku}: characteristic {title!r} skipped: {exc}")
                        continue
                    characteristics.append({
                        "characteristic_id": cid,
                        "value": value,
                        "values": [value],
                    })

                dedup = {x["characteristic_id"]: x for x in characteristics}
                patch = {"characteristics": list(dedup.values())}
                description = s((info or {}).get("description") if isinstance(info, dict) else "")
                if description:
                    patch["description"] = description
                kit.patch_variant(variant_id, patch)
                report["characteristics_written"] += len(dedup)

                media = []
                for url in image_urls(info):
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
                        if len(report["warnings"]) < 200:
                            report["warnings"].append(f"{sku}: image failed: {exc}")
                if media:
                    kit.patch_variant(variant_id, {"media": media})

                if source_article:
                    mapping.setdefault("variants", {}).setdefault(source_article, []).append({
                        "variant_id": variant_id,
                        "kit_id": kit_id,
                        "sku": sku,
                    })
                else:
                    report["created_without_source_match"] += 1

                report["created_in_kit"] += 1
                report["created"].append({
                    "sku": sku,
                    "kit_id": kit_id,
                    "name": name,
                    "code_for_site": code_value,
                    "source_matched": bool(source_article),
                })

            except Exception as exc:
                report["errors"].append({
                    "product_id": product_id,
                    "sku": sku,
                    "name": s(product.get("name")),
                    "message": str(exc)[:1000],
                })

    mod.save_mapping(mapping)
    report["finished_at"] = now_iso()
    report["complete"] = not report["errors"]
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
