#!/usr/bin/env python3
import importlib.util
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "norden_chairs_stools_duplicate_audit.json"

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
    return any(("кресл" in p) or ("стул" in p) for p in parts)

def canonical_key(mod, row):
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

def main():
    mod = load_sync()
    kit = mod.KitClient(os.environ.get("YANDEX_KIT_TOKEN",""))
    source, _, source_kind, api_error = mod.load_source(
        os.environ.get("NORDEN_SECRET",""), short=False
    )
    target = {a:i for a,i in source.items() if category_match(i)}
    if not target:
        raise RuntimeError("Не найдены категории кресел/стульев в источнике Norden")

    chars = kit.characteristics()
    identity_char_ids = mod.resolve_identity_characteristic_ids(chars)
    source_index = mod.build_source_identity_index(source)

    groups = defaultdict(list)
    conflicts = []
    unresolved = []
    scanned = 0
    norden_nonarchived = 0

    for row in kit.variants_parallel(workers=6):
        scanned += 1
        if mod.s(row.get("brand")).casefold() != mod.BRAND.casefold():
            continue
        if mod.s(row.get("status")).upper() == "ARCHIVED":
            continue
        norden_nonarchived += 1

        article, matched, values, conflict = mod.match_source_by_identity(
            row, source_index, identity_char_ids
        )
        if conflict:
            conflicts.append({
                "sku": mod.s(row.get("sku")),
                "kit_id": row.get("kit_id"),
                "status": mod.s(row.get("status")),
                "name": mod.s(row.get("name")),
                "identity_conflict": conflict,
            })
            continue
        if not article:
            continue
        if article not in target:
            continue

        groups[article].append({
            "variant_id": mod.s(row.get("id")),
            "kit_id": row.get("kit_id"),
            "sku": mod.s(row.get("sku")),
            "status": mod.s(row.get("status")),
            "name": mod.s(row.get("name")),
            "matched_identifiers": matched,
        })

    duplicate_groups = []
    duplicate_variant_ids = set()
    for article, rows in sorted(groups.items()):
        unique = {r["variant_id"]: r for r in rows if r["variant_id"]}
        rows = list(unique.values())
        if len(rows) <= 1:
            continue
        rows.sort(key=lambda r: canonical_key(mod, r))
        keep = rows[0]
        dupes = rows[1:]
        for r in dupes:
            duplicate_variant_ids.add(r["variant_id"])
        duplicate_groups.append({
            "article": article,
            "category_path": target[article].get("category_path") or [],
            "kept": keep,
            "duplicates": dupes,
        })

    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source": source_kind,
        "source_api_error": api_error,
        "scope": "Norden chairs/stools only",
        "filter": "supplier category_path contains кресл* or стул*",
        "name_matching_used": False,
        "write_actions": False,
        "source_target_products": len(target),
        "kit_variants_scanned": scanned,
        "norden_nonarchived_seen": norden_nonarchived,
        "matched_target_articles": len(groups),
        "duplicate_groups": len(duplicate_groups),
        "duplicate_variant_ids_total": len(duplicate_variant_ids),
        "identity_conflicts_total": len(conflicts),
        "canonical_rule": "legacy/non-100 first; then PUBLISHED; then lowest KIT ID",
        "groups": duplicate_groups,
        "conflicts": conflicts,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
        "source_target_products": report["source_target_products"],
        "matched_target_articles": report["matched_target_articles"],
        "duplicate_groups": report["duplicate_groups"],
        "duplicate_variant_ids_total": report["duplicate_variant_ids_total"],
        "identity_conflicts_total": report["identity_conflicts_total"],
        "write_actions": report["write_actions"],
    },ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
