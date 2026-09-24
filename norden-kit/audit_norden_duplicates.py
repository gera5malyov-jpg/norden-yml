#!/usr/bin/env python3
import importlib.util
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "norden_duplicate_audit_report.json"


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

    source, source_dups, source_kind, api_error = mod.load_source(
        os.environ.get("NORDEN_SECRET", ""),
        short=False,
    )
    source_index = mod.build_source_identity_index(source)

    chars = kit.characteristics()
    identity_char_ids = mod.resolve_identity_characteristic_ids(chars)

    by_id = {}
    hidden_by_id = {}
    scanned = 0
    duplicate_listing_rows = 0
    for row in kit.variants_parallel(workers=6):
        scanned += 1
        if mod.s(row.get("brand")).casefold() != mod.BRAND.casefold():
            continue
        status = mod.s(row.get("status")).upper()
        if status == "ARCHIVED":
            continue
        vid = mod.s(row.get("id"))
        if not vid:
            continue
        if status == "HIDDEN":
            hidden_by_id[vid] = row
            continue
        if status != "PUBLISHED":
            continue
        if vid in by_id:
            duplicate_listing_rows += 1
        by_id[vid] = row

    by_field = defaultdict(lambda: defaultdict(list))
    by_source_article = defaultdict(list)
    unresolved = []
    conflicts = []

    def compact(row, matched_article="", identity_values=None):
        return {
            "variant_id": mod.s(row.get("id")),
            "kit_id": row.get("kit_id"),
            "sku": mod.s(row.get("sku")),
            "name": mod.s(row.get("name")),
            "status": mod.s(row.get("status")),
            "norden_article": matched_article,
            "identity_values": identity_values or [],
        }

    for row in by_id.values():
        values = []
        sku = mod.s(row.get("sku"))
        if sku:
            values.append(("SKU", sku))
            by_field["SKU"][mod.norm_code(sku)].append(compact(row))

        for title, ids in identity_char_ids.items():
            for cid in ids:
                value = mod.current_char_value(row, cid)
                if not value:
                    continue
                values.append((title, value))
                by_field[title][mod.norm_code(value)].append(compact(row))

        article, matched, all_values, conflict = mod.match_source_by_identity(
            row, source_index, identity_char_ids
        )
        if article:
            by_source_article[article].append(
                compact(row, article, [{"field": a, "value": b} for a, b in all_values])
            )
        else:
            item = compact(row, "", [{"field": a, "value": b} for a, b in all_values])
            if conflict:
                item["identity_conflict"] = conflict
                conflicts.append(item)
            else:
                unresolved.append(item)

    def duplicate_groups(index):
        out = []
        for key, rows in index.items():
            if not key:
                continue
            unique = {r["variant_id"]: r for r in rows}
            if len(unique) > 1:
                out.append({
                    "key": key,
                    "count": len(unique),
                    "items": list(unique.values()),
                })
        out.sort(key=lambda x: (-x["count"], str(x["key"])))
        return out

    duplicates_by_field = {
        field: duplicate_groups(index)
        for field, index in by_field.items()
    }
    source_article_dups = duplicate_groups(by_source_article)

    duplicate_variant_ids = set()
    for groups in list(duplicates_by_field.values()) + [source_article_dups]:
        for group in groups:
            for item in group["items"]:
                duplicate_variant_ids.add(item["variant_id"])

    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "brand": mod.BRAND,
        "source": source_kind,
        "source_api_error": api_error,
        "source_products": len(source),
        "source_duplicate_articles": sorted(set(source_dups)),
        "kit_variants_scanned": scanned,
        "published_norden_variants": len(by_id),
        "hidden_norden_variants": len(hidden_by_id),
        "active_norden_variants": len(by_id),
        "duplicate_listing_rows_collapsed": duplicate_listing_rows,
        "name_matching_policy": "NO",
        "identity_fields_checked": identity_char_ids,
        "duplicate_groups_by_source_article": len(source_article_dups),
        "duplicate_groups_by_field": {
            field: len(groups) for field, groups in duplicates_by_field.items()
        },
        "duplicate_variant_ids_total": len(duplicate_variant_ids),
        "identity_conflicts": len(conflicts),
        "unresolved_active_norden": len(unresolved),
        "duplicates_by_source_article": source_article_dups,
        "duplicates_by_field": duplicates_by_field,
        "identity_conflict_sample": conflicts[:100],
        "unresolved_sample": unresolved[:100],
        "complete": True,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "source_products": report["source_products"],
        "kit_variants_scanned": report["kit_variants_scanned"],
        "published_norden_variants": report["published_norden_variants"],
        "hidden_norden_variants": report["hidden_norden_variants"],
        "active_norden_variants": report["active_norden_variants"],
        "duplicate_listing_rows_collapsed": report["duplicate_listing_rows_collapsed"],
        "duplicate_groups_by_source_article": report["duplicate_groups_by_source_article"],
        "duplicate_groups_by_field": report["duplicate_groups_by_field"],
        "duplicate_variant_ids_total": report["duplicate_variant_ids_total"],
        "identity_conflicts": report["identity_conflicts"],
        "unresolved_active_norden": report["unresolved_active_norden"],
        "name_matching_policy": report["name_matching_policy"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
