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
    kit = mod.KitClient(os.environ.get("YANDEX_KIT_TOKEN",""))
    source, _, source_kind, api_error = mod.load_source(
        os.environ.get("NORDEN_SECRET",""), short=False
    )
    target = {a:i for a,i in source.items() if category_match(i)}
    if not target:
        raise RuntimeError("Не найдены категории кресел/стульев в источнике Norden")

    base = json.loads(FULL_AUDIT.read_text(encoding="utf-8"))
    base_groups = base.get("duplicates_by_source_article") or []

    candidate_groups = []
    candidate_variant_ids = set()
    for g in base_groups:
        article = mod.s(g.get("key"))
        if article not in target:
            continue
        rows = g.get("items") or []
        if len(rows) <= 1:
            continue
        candidate_groups.append((article, rows))
        for r in rows:
            vid = mod.s(r.get("variant_id"))
            if vid:
                candidate_variant_ids.add(vid)

    chars = kit.characteristics()
    identity_char_ids = mod.resolve_identity_characteristic_ids(chars)
    source_index = mod.build_source_identity_index(source)

    duplicate_groups = []
    identity_conflicts = []
    live_reads = 0

    for article, seed_rows in candidate_groups:
        live_rows = []
        for seed in seed_rows:
            vid = mod.s(seed.get("variant_id"))
            if not vid:
                continue
            try:
                row = kit.get_variant(vid)
                live_reads += 1
            except Exception:
                continue
            if mod.s(row.get("brand")).casefold() != mod.BRAND.casefold():
                continue
            if mod.s(row.get("status")).upper() == "ARCHIVED":
                continue

            live_article, matched, values, conflict = mod.match_source_by_identity(
                row, source_index, identity_char_ids
            )
            if conflict:
                identity_conflicts.append({
                    "sku": mod.s(row.get("sku")),
                    "kit_id": row.get("kit_id"),
                    "status": mod.s(row.get("status")),
                    "name": mod.s(row.get("name")),
                    "identity_conflict": conflict,
                })
                continue
            if live_article != article:
                continue

            live_rows.append({
                "variant_id": mod.s(row.get("id")),
                "kit_id": row.get("kit_id"),
                "sku": mod.s(row.get("sku")),
                "status": mod.s(row.get("status")),
                "name": mod.s(row.get("name")),
                "matched_identifiers": matched,
            })

        unique = {r["variant_id"]: r for r in live_rows if r["variant_id"]}
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
        "source": source_kind,
        "source_api_error": api_error,
        "scope": "Norden chairs/stools only",
        "strategy": "reuse latest Norden-only duplicate index; live-read candidates only",
        "full_catalog_scan": False,
        "base_norden_variants": base.get("active_norden_variants"),
        "source_target_products": len(target),
        "candidate_groups_from_norden_index": len(candidate_groups),
        "candidate_variant_ids": len(candidate_variant_ids),
        "live_variant_reads": live_reads,
        "duplicate_groups": len(duplicate_groups),
        "duplicate_variant_ids_total": len(duplicate_variant_ids),
        "identity_conflicts_total": len(identity_conflicts),
        "name_matching_used": False,
        "write_actions": False,
        "canonical_rule": "legacy/non-100 first; then PUBLISHED; then lowest KIT ID",
        "groups": duplicate_groups,
        "conflicts": identity_conflicts,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
        "base_norden_variants": report["base_norden_variants"],
        "candidate_groups_from_norden_index": report["candidate_groups_from_norden_index"],
        "candidate_variant_ids": report["candidate_variant_ids"],
        "live_variant_reads": report["live_variant_reads"],
        "duplicate_groups": report["duplicate_groups"],
        "duplicate_variant_ids_total": report["duplicate_variant_ids_total"],
        "identity_conflicts_total": report["identity_conflicts_total"],
        "full_catalog_scan": report["full_catalog_scan"],
    },ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
