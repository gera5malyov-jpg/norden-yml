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
        short=True,
    )
    source_articles = list(source)

    chars = kit.characteristics()
    code_matches = [
        row for row in chars
        if mod.norm_title(row.get("title")) == mod.norm_title(mod.CODE_SITE_TITLE)
    ]
    if len(code_matches) != 1:
        raise RuntimeError(
            f"Expected exactly one KIT characteristic {mod.CODE_SITE_TITLE!r}, found {len(code_matches)}"
        )
    code_site_id = mod.s(code_matches[0].get("id"))

    by_id = {}
    scanned = 0
    duplicate_listing_rows = 0
    for row in kit.variants_parallel(workers=6):
        scanned += 1
        if mod.s(row.get("brand")).casefold() != mod.BRAND.casefold():
            continue
        if mod.s(row.get("status")).upper() == "ARCHIVED":
            continue
        vid = mod.s(row.get("id"))
        if not vid:
            continue
        if vid in by_id:
            duplicate_listing_rows += 1
        by_id[vid] = row

    by_sku = defaultdict(list)
    by_code = defaultdict(list)
    by_article = defaultdict(list)
    unresolved = []

    def compact(row, code="", article=""):
        return {
            "variant_id": mod.s(row.get("id")),
            "kit_id": row.get("kit_id"),
            "sku": mod.s(row.get("sku")),
            "name": mod.s(row.get("name")),
            "code_for_site": code,
            "norden_article": article,
        }

    for row in by_id.values():
        sku = mod.s(row.get("sku"))
        code = mod.current_char_value(row, code_site_id)
        article = mod.match_source_article(code, source_articles) if code else None

        if sku:
            by_sku[sku].append(compact(row, code, article or ""))
        if code:
            by_code[mod.norm_code(code)].append(compact(row, code, article or ""))
        if article:
            by_article[article].append(compact(row, code, article))
        else:
            unresolved.append(compact(row, code, ""))

    def duplicate_groups(index):
        out = []
        for key, rows in index.items():
            unique = {r["variant_id"]: r for r in rows}
            if len(unique) > 1:
                out.append({
                    "key": key,
                    "count": len(unique),
                    "items": list(unique.values()),
                })
        out.sort(key=lambda x: (-x["count"], str(x["key"])))
        return out

    sku_dups = duplicate_groups(by_sku)
    code_dups = duplicate_groups(by_code)
    article_dups = duplicate_groups(by_article)

    duplicate_variant_ids = set()
    for group in article_dups + code_dups + sku_dups:
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
        "active_norden_variants": len(by_id),
        "duplicate_listing_rows_collapsed": duplicate_listing_rows,
        "duplicate_groups_by_norden_article": len(article_dups),
        "duplicate_groups_by_code_for_site": len(code_dups),
        "duplicate_groups_by_exact_sku": len(sku_dups),
        "duplicate_variant_ids_total": len(duplicate_variant_ids),
        "unresolved_active_norden": len(unresolved),
        "duplicates_by_norden_article": article_dups,
        "duplicates_by_code_for_site": code_dups,
        "duplicates_by_exact_sku": sku_dups,
        "unresolved_sample": unresolved[:100],
        "complete": True,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        k: report[k] for k in (
            "source_products",
            "kit_variants_scanned",
            "active_norden_variants",
            "duplicate_listing_rows_collapsed",
            "duplicate_groups_by_norden_article",
            "duplicate_groups_by_code_for_site",
            "duplicate_groups_by_exact_sku",
            "duplicate_variant_ids_total",
            "unresolved_active_norden",
        )
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
