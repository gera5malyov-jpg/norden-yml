#!/usr/bin/env python3
import importlib.util
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "norden_duplicate_cleanup_report.json"


def load_sync():
    path = ROOT / "sync_norden_kit.py"
    spec = importlib.util.spec_from_file_location("norden_sync", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    mod = load_sync()
    kit = mod.KitClient(os.environ.get("YANDEX_KIT_TOKEN", ""))
    source, _, source_kind, api_error = mod.load_source(
        os.environ.get("NORDEN_SECRET", ""),
        short=False,
    )
    source_index = mod.build_source_identity_index(source)
    chars = kit.characteristics()
    identity_char_ids = mod.resolve_identity_characteristic_ids(chars)
    warehouses = {
        mod.s(x.get("title")): mod.s(x.get("id"))
        for x in kit.warehouses()
        if mod.s(x.get("id")) and mod.s(x.get("status")).upper() != "ARCHIVED"
    }

    groups = defaultdict(list)
    conflicts = []
    scanned = 0
    norden_seen = 0

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
                "name": mod.s(row.get("name")),
                "status": mod.s(row.get("status")),
                "identity_conflict": conflict,
            })
            continue
        if not article:
            continue
        groups[article].append({
            "variant_id": mod.s(row.get("id")),
            "kit_id": row.get("kit_id"),
            "sku": mod.s(row.get("sku")),
            "name": mod.s(row.get("name")),
            "status": mod.s(row.get("status")),
            "created_at": mod.s(row.get("created_at")),
            "matched_identifiers": matched,
        })

    duplicate_groups = {
        article: rows for article, rows in groups.items()
        if len({x["variant_id"] for x in rows if x["variant_id"]}) > 1
    }

    stock_rows = []
    to_hide = []
    cleaned_groups = []
    skipped_groups = []

    def canonical_key(row):
        sku = mod.s(row.get("sku"))
        status = mod.s(row.get("status")).upper()
        try:
            kit_id = int(row.get("kit_id"))
        except Exception:
            kit_id = 10**18
        # Preserve legacy/original supplier-era cards first; then prefer currently
        # published cards; then oldest (lowest KIT id).
        return (
            1 if sku.startswith("100-") else 0,
            1 if status != "PUBLISHED" else 0,
            kit_id,
            sku,
        )

    for article, rows in sorted(duplicate_groups.items()):
        unique = {x["variant_id"]: x for x in rows if x["variant_id"]}
        rows = list(unique.values())
        if len(rows) <= 1:
            continue

        rows.sort(key=canonical_key)
        keep = rows[0]
        drop = rows[1:]

        # Safety: only clean rows that all resolved uniquely to the same source article.
        if not keep.get("variant_id") or any(not x.get("variant_id") for x in drop):
            skipped_groups.append({"article": article, "reason": "missing variant id", "rows": rows})
            continue

        for row in drop:
            for wid in warehouses.values():
                stock_rows.append({
                    "variant_id": row["variant_id"],
                    "warehouse_id": wid,
                    "quantity": 0,
                })
            if mod.s(row.get("status")).upper() != "HIDDEN":
                to_hide.append(row)

        cleaned_groups.append({
            "article": article,
            "kept": keep,
            "duplicates": drop,
        })

    zero_errors = []
    for start in range(0, len(stock_rows), 5000):
        batch = stock_rows[start:start+5000]
        try:
            kit.bulk_stocks(batch)
        except Exception as exc:
            zero_errors.append(str(exc)[:1000])

    hide_errors = []
    hidden = 0
    for i, row in enumerate(to_hide, 1):
        try:
            kit.patch_variant(row["variant_id"], {"status": "HIDDEN"})
            hidden += 1
        except Exception as exc:
            hide_errors.append({
                "sku": row.get("sku"),
                "kit_id": row.get("kit_id"),
                "variant_id": row.get("variant_id"),
                "error": str(exc)[:1000],
            })
        if i % 100 == 0:
            print(f"Norden duplicate cleanup hidden: {i}/{len(to_hide)}", flush=True)

    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source": source_kind,
        "source_api_error": api_error,
        "kit_variants_scanned": scanned,
        "norden_variants_seen": norden_seen,
        "identity_conflicts_skipped": len(conflicts),
        "duplicate_groups_found": len(duplicate_groups),
        "duplicate_groups_cleaned": len(cleaned_groups),
        "duplicate_groups_skipped": len(skipped_groups),
        "canonical_rule": "legacy/non-100 first; then PUBLISHED; then lowest KIT ID",
        "duplicates_zeroed_planned": len({x["variant_id"] for g in cleaned_groups for x in g["duplicates"]}),
        "stock_zero_rows": len(stock_rows),
        "duplicates_hidden": hidden,
        "hide_errors": hide_errors,
        "stock_zero_errors": zero_errors,
        "conflict_sample": conflicts[:100],
        "skipped_sample": skipped_groups[:100],
        "cleaned_sample": cleaned_groups[:100],
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "complete": not zero_errors and not hide_errors,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        k: report[k] for k in (
            "duplicate_groups_found",
            "duplicate_groups_cleaned",
            "duplicate_groups_skipped",
            "duplicates_zeroed_planned",
            "duplicates_hidden",
            "identity_conflicts_skipped",
            "complete",
        )
    }, ensure_ascii=False, indent=2))
    if zero_errors or hide_errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
