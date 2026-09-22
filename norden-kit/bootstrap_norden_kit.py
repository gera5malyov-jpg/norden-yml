#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import time
from pathlib import Path

from sync_norden_kit import (
    BRAND,
    KitClient,
    ensure_category_path,
    load_mapping,
    load_source,
    now_iso,
    price_set,
    resolve_special_characteristics,
    resolve_warehouses,
    s,
    save_mapping,
)

ROOT = Path(__file__).resolve().parent
REPORT_PATH = ROOT / "bootstrap_report.json"


def load_source_retry(secret, attempts=4):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return load_source(secret, short=False)
        except Exception as exc:
            last = exc
            if attempt < attempts:
                time.sleep(min(30, 3 * attempt))
    raise last


def create_fast(kit, item, categories, code_site_id, article_id, warehouses, report):
    prices = price_set(item.get("purchase"))
    if not prices:
        raise RuntimeError("Нет положительной закупочной цены; карточка пропущена до появления цены у Norden")

    category_id = ensure_category_path(kit, categories, item.get("category_path") or ["Norden"])
    product = kit.create_product(category_id)
    product_id = s(product.get("id"))
    if not product_id:
        raise RuntimeError("KIT did not return product id")

    safe_part = re.sub(r"[^0-9A-Za-z]+", "-", item["article"])[:45].strip("-") or "ITEM"
    suffix = hashlib.sha1(item["article"].encode("utf-8")).hexdigest()[:10]
    temp_sku = f"NORDEN-TMP-{safe_part}-{suffix}"

    body = {
        "sku": temp_sku,
        "name": item["name"],
        "status": "PUBLISHED",
        "product_id": product_id,
        "brand": BRAND,
        "pricing": {
            "price": str(prices["old"]),
            "manual_discount_price": str(prices["sale"]),
        },
    }
    stock = item.get("stock")
    if stock is not None:
        body["stocks"] = [
            {"warehouse_id": wid, "quantity": int(stock), "reserved": 0}
            for wid in warehouses.values()
        ]

    created = kit.create_variant(body)
    variant_id = s(created.get("id"))
    if not variant_id:
        raise RuntimeError("KIT did not return variant id")

    kit_id = created.get("kit_id")
    if kit_id in (None, ""):
        created = kit.get_variant(variant_id)
        kit_id = created.get("kit_id")
    if kit_id in (None, ""):
        raise RuntimeError("KIT did not return kit_id for new variant")

    final_article = f"100-{kit_id}"
    final_chars = [
        {"characteristic_id": code_site_id, "value": item["article"], "values": [item["article"]]},
        {"characteristic_id": article_id, "value": final_article, "values": [final_article]},
    ]

    # Variant already exists at this point. Never fail the whole item on a cosmetic SKU patch:
    # keeping the mapping prevents duplicate creation on the next bootstrap batch.
    try:
        kit.patch_variant(variant_id, {"sku": final_article, "characteristics": final_chars})
    except Exception as exc:
        try:
            kit.patch_variant(variant_id, {"characteristics": final_chars})
        except Exception as exc2:
            report["warnings"].append(
                f"{item['article']}: создан variant_id={variant_id}, но не удалось записать характеристики: {exc2}"
            )
        report["warnings"].append(
            f"{item['article']}: не удалось заменить временный SKU на {final_article}: {exc}"
        )

    return {
        "variant_id": variant_id,
        "kit_id": kit_id,
        "sku": final_article,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-new", type=int, default=600)
    args = ap.parse_args()

    token = __import__("os").environ.get("YANDEX_KIT_TOKEN", "")
    secret = __import__("os").environ.get("NORDEN_SECRET", "").strip()

    report = {
        "started_at": now_iso(),
        "status": "running",
        "source": None,
        "source_products": 0,
        "mapped_before": 0,
        "missing_before": 0,
        "eligible_missing_before": 0,
        "missing_without_price": 0,
        "new_products_created": 0,
        "remaining_total": 0,
        "remaining_eligible": 0,
        "warnings": [],
        "errors": [],
    }

    kit = KitClient(token)
    warehouses = resolve_warehouses(kit)
    source, duplicates, source_kind, api_error = load_source_retry(secret)
    report["source"] = source_kind
    report["api_error"] = api_error
    report["source_products"] = len(source)
    report["source_duplicate_articles"] = len(set(duplicates))

    if len(source) < 1000:
        raise RuntimeError(f"Safety stop: unexpectedly small Norden source ({len(source)} products)")

    _, _, code_site_id, article_id = resolve_special_characteristics(kit)
    mapping = load_mapping()
    mapping.setdefault("variants", {})

    missing = [a for a in source if a not in mapping["variants"]]
    eligible = [a for a in missing if price_set(source[a].get("purchase"))]
    no_price = [a for a in missing if not price_set(source[a].get("purchase"))]

    report["mapped_before"] = len(mapping["variants"])
    report["missing_before"] = len(missing)
    report["eligible_missing_before"] = len(eligible)
    report["missing_without_price"] = len(no_price)
    report["missing_without_price_sample"] = no_price[:100]

    categories = kit.categories()
    for article in eligible[: max(0, args.max_new)]:
        item = source[article]
        try:
            new = create_fast(
                kit, item, categories, code_site_id, article_id, warehouses, report
            )
            mapping["variants"][article] = [new]
            save_mapping(mapping)
            report["new_products_created"] += 1
        except Exception as exc:
            report["errors"].append({
                "article": article,
                "stage": "create_fast",
                "message": str(exc)[:800],
            })
        if len(report["errors"]) >= 100:
            report["warnings"].append("Достигнут лимит 100 ошибок; текущий пакет остановлен.")
            break

    remaining = [a for a in source if a not in mapping["variants"]]
    remaining_eligible = [a for a in remaining if price_set(source[a].get("purchase"))]
    remaining_no_price = [a for a in remaining if not price_set(source[a].get("purchase"))]

    mapping["initial_complete"] = len(remaining) == 0
    save_mapping(mapping)

    report["remaining_total"] = len(remaining)
    report["remaining_eligible"] = len(remaining_eligible)
    report["remaining_without_price"] = len(remaining_no_price)
    report["initial_complete"] = mapping["initial_complete"]
    report["status"] = "ok" if not report["errors"] else "completed_with_errors"
    report["finished_at"] = now_iso()
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        failure = {
            "finished_at": now_iso(),
            "status": "failed",
            "message": str(exc),
        }
        REPORT_PATH.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        raise
