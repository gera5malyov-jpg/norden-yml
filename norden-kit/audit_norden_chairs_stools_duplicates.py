#!/usr/bin/env python3
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FULL_AUDIT = ROOT / "norden_duplicate_audit_report.json"
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
    if not FULL_AUDIT.exists():
        raise RuntimeError("Нет свежего полного Norden-only аудита; быстрый аудит невозможен")

    mod = load_sync()
    # XML is used here deliberately: one direct supplier snapshot, no paginated API delay.
    source, source_dups = mod.source_from_xml(short=False)
    target = {a:i for a,i in source.items() if category_match(i)}
    if not target:
        raise RuntimeError("Не найдены категории кресел/стульев в источнике Norden")

    base = json.loads(FULL_AUDIT.read_text(encoding="utf-8"))
    base_groups = base.get("duplicates_by_source_article") or []

    duplicate_groups = []
    candidate_variant_ids = set()

    for g in base_groups:
        article = mod.s(g.get("key"))
        if article not in target:
            continue
        seed_rows = g.get("items") or []
        unique = {}
        for seed in seed_rows:
            vid = mod.s(seed.get("variant_id"))
            if not vid:
                continue
            if mod.s(seed.get("status")).upper() == "ARCHIVED":
                continue
            unique[vid] = {
                "variant_id": vid,
                "kit_id": seed.get("kit_id"),
                "sku": mod.s(seed.get("sku")),
                "status": mod.s(seed.get("status")),
                "name": mod.s(seed.get("name")),
                "matched_identifiers": seed.get("identity_values") or [],
            }
            candidate_variant_ids.add(vid)

        rows = list(unique.values())
        if len(rows) <= 1:
            continue
        rows.sort(key=lambda r: canonical_key(mod, r))
        duplicate_groups.append({
            "article": article,
            "category_path": target[article].get("category_path") or [],
            "kept": rows[0],
            "duplicates": rows[1:],
        })

    duplicate_variant_ids = {
        r["variant_id"]
        for g in duplicate_groups
        for r in (g.get("duplicates") or [])
        if r.get("variant_id")
    }

    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source": "xml-full+price",
        "source_duplicate_articles": len(source_dups),
        "scope": "Norden chairs/stools only",
        "strategy": "fresh Norden-only duplicate index + supplier XML category filter; no full KIT scan",
        "full_catalog_scan": False,
        "brand_scope": "Norden only",
        "base_norden_variants": base.get("active_norden_variants"),
        "base_audit_started_at": base.get("started_at"),
        "source_target_products": len(target),
        "candidate_variant_ids": len(candidate_variant_ids),
        "live_variant_reads": 0,
        "duplicate_groups": len(duplicate_groups),
        "duplicate_variant_ids_total": len(duplicate_variant_ids),
        "identity_conflicts_total": 0,
        "name_matching_used": False,
        "write_actions": False,
        "live_recheck_required_before_archive": True,
        "canonical_rule": "legacy/non-100 first; then PUBLISHED; then lowest KIT ID",
        "groups": duplicate_groups,
        "conflicts": [],
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
        "brand_scope": report["brand_scope"],
        "base_norden_variants": report["base_norden_variants"],
        "source_target_products": report["source_target_products"],
        "candidate_variant_ids": report["candidate_variant_ids"],
        "duplicate_groups": report["duplicate_groups"],
        "duplicate_variant_ids_total": report["duplicate_variant_ids_total"],
        "live_variant_reads": report["live_variant_reads"],
        "full_catalog_scan": report["full_catalog_scan"],
        "live_recheck_required_before_archive": report["live_recheck_required_before_archive"],
    },ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
