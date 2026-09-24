#!/usr/bin/env python3
import importlib.util
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "norden_chairs_stools_update_report.json"

def load_sync():
    path = ROOT / "sync_norden_kit.py"
    spec = importlib.util.spec_from_file_location("norden_sync", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def category_match(item):
    parts = [str(x or "").strip().casefold().replace("ё","е") for x in (item.get("category_path") or [])]
    # Supplier categories only. Product name is intentionally not used.
    return any(("кресл" in p) or ("стул" in p) for p in parts)

def main():
    mod = load_sync()
    kit = mod.KitClient(os.environ.get("YANDEX_KIT_TOKEN",""))
    source, source_dups, source_kind, api_error = mod.load_source(
        os.environ.get("NORDEN_SECRET",""), short=False
    )
    target = {a:i for a,i in source.items() if category_match(i)}
    if not target:
        raise RuntimeError("В источнике Norden не найдены категории кресел/стульев")

    chars = kit.characteristics()
    identity_char_ids = mod.resolve_identity_characteristic_ids(chars)
    source_index = mod.build_source_identity_index(source)
    warehouses = mod.resolve_warehouses(kit)

    groups = defaultdict(list)
    conflicts = []
    scanned = 0
    norden_seen = 0
    target_seen = 0

    for row in kit.variants_parallel(workers=6):
        scanned += 1
        if mod.s(row.get("brand")).casefold() != mod.BRAND.casefold():
            continue
        if mod.s(row.get("status")).upper() == "ARCHIVED":
            continue
        norden_seen += 1
        article, matched, values, conflict = mod.match_source_by_identity(
            row, source_index, identity_char_ids
        )
        if conflict:
            conflicts.append({
                "sku": mod.s(row.get("sku")),
                "kit_id": row.get("kit_id"),
                "identity_conflict": conflict,
            })
            continue
        if not article or article not in target:
            continue
        target_seen += 1
        groups[article].append({
            "variant_id": mod.s(row.get("id")),
            "kit_id": row.get("kit_id"),
            "sku": mod.s(row.get("sku")),
            "status": mod.s(row.get("status")),
        })

    def canonical_key(row):
        sku = mod.s(row.get("sku"))
        status = mod.s(row.get("status")).upper()
        try:
            kid = int(row.get("kit_id"))
        except Exception:
            kid = 10**18
        return (
            1 if sku.startswith("100-") else 0,
            1 if status != "PUBLISHED" else 0,
            kid,
            sku,
        )

    price_rows=[]
    stock_rows=[]
    duplicate_stock_rows=[]
    duplicate_groups=[]
    mapped=0

    for article, rows in groups.items():
        unique={r["variant_id"]:r for r in rows if r["variant_id"]}
        rows=list(unique.values())
        if not rows:
            continue
        rows.sort(key=canonical_key)
        keep=rows[0]
        drop=rows[1:]
        if drop:
            duplicate_groups.append({"article":article,"kept":keep,"duplicates":drop})
            for r in drop:
                for wid in warehouses.values():
                    duplicate_stock_rows.append({
                        "variant_id":r["variant_id"],
                        "warehouse_id":wid,
                        "quantity":0,
                    })
        item=target[article]
        ps=mod.price_set(item.get("purchase"))
        if ps:
            price_rows.append({"variant_id":keep["variant_id"],**ps})
        if item.get("stock") is not None:
            for wid in warehouses.values():
                stock_rows.append({
                    "variant_id":keep["variant_id"],
                    "warehouse_id":wid,
                    "quantity":int(item["stock"]),
                })
        mapped += 1

    # First quarantine duplicate stock within the selected categories, then update canonical cards.
    stale_dup = kit.bulk_stocks(duplicate_stock_rows) if duplicate_stock_rows else []
    min_field = None
    stale_prices=[]
    stale_stocks=[]
    if price_rows:
        min_field=kit.discover_minimum_price_field(price_rows[0])
        stale_prices=kit.bulk_prices(price_rows, minimum_field=min_field)
    if stock_rows:
        stale_stocks=kit.bulk_stocks(stock_rows)

    report={
        "started_at":datetime.now(timezone.utc).isoformat(),
        "source":source_kind,
        "source_api_error":api_error,
        "mode":"existing-only chairs/stools",
        "filter":"supplier category_path contains кресл* or стул*",
        "name_matching_used":False,
        "new_product_creation":False,
        "source_products_total":len(source),
        "source_target_products":len(target),
        "kit_variants_scanned":scanned,
        "active_norden_seen":norden_seen,
        "target_variant_matches_seen":target_seen,
        "target_articles_mapped":mapped,
        "target_articles_unmapped":len(set(target)-set(groups)),
        "price_updates":len(price_rows),
        "stock_updates":len(stock_rows),
        "duplicate_groups_in_target":len(duplicate_groups),
        "duplicate_variants_zeroed":sum(len(g["duplicates"]) for g in duplicate_groups),
        "identity_conflicts_skipped":len(conflicts),
        "minimum_price_field":min_field,
        "stale_price_variant_ids":stale_prices,
        "stale_stock_variant_ids":stale_stocks,
        "stale_duplicate_variant_ids":stale_dup,
        "duplicate_sample":duplicate_groups[:100],
        "conflict_sample":conflicts[:100],
        "complete":True,
        "finished_at":datetime.now(timezone.utc).isoformat(),
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
        k:report[k] for k in (
            "source_target_products","target_articles_mapped","target_articles_unmapped",
            "price_updates","stock_updates","duplicate_groups_in_target",
            "duplicate_variants_zeroed","identity_conflicts_skipped","complete"
        )
    },ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
