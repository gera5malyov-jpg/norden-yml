#!/usr/bin/env python3
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AUDIT = ROOT / "norden_chairs_stools_duplicate_audit.json"
REPORT = ROOT / "norden_chairs_stools_archive_report.json"

def load_sync():
    path = ROOT / "sync_norden_kit.py"
    spec = importlib.util.spec_from_file_location("norden_sync", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def main():
    if not AUDIT.exists():
        raise RuntimeError("Нет read-only отчёта проверки кресел/стульев — архивирование запрещено")

    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    if audit.get("write_actions") is not False:
        raise RuntimeError("Некорректный аудит: ожидался read-only отчёт")

    mod = load_sync()
    kit = mod.KitClient(os.environ.get("YANDEX_KIT_TOKEN",""))
    source, source_dups = mod.source_from_xml(short=False)
    source_kind = "xml-full+price"
    api_error = None
    source_index = mod.build_source_identity_index(source)
    identity_char_ids = mod.resolve_identity_characteristic_ids(kit.characteristics())

    warehouse_ids = [
        mod.s(w.get("id")) for w in kit.warehouses()
        if mod.s(w.get("id"))
    ]

    planned = []
    for group in audit.get("groups") or []:
        article = mod.s(group.get("article"))
        kept = group.get("kept") or {}
        for dup in group.get("duplicates") or []:
            planned.append({
                "article": article,
                "kept": kept,
                "duplicate": dup,
            })

    verified = []
    skipped = []
    for item in planned:
        article = item["article"]
        dup = item["duplicate"]
        vid = mod.s(dup.get("variant_id"))
        if not vid:
            skipped.append({**item, "reason":"missing variant_id"})
            continue
        try:
            row = kit.get_variant(vid)
        except Exception as exc:
            skipped.append({**item, "reason":f"cannot read live variant: {str(exc)[:500]}"})
            continue

        status = mod.s(row.get("status")).upper()
        if status == "ARCHIVED":
            skipped.append({**item, "reason":"already archived"})
            continue
        if mod.s(row.get("brand")).casefold() != mod.BRAND.casefold():
            skipped.append({**item, "reason":"brand changed"})
            continue

        live_article, matched, values, conflict = mod.match_source_by_identity(
            row, source_index, identity_char_ids
        )
        if conflict or live_article != article:
            skipped.append({
                **item,
                "reason":"live identity no longer confirms same Norden article",
                "live_article":live_article,
                "conflict":conflict,
            })
            continue
        verified.append({
            **item,
            "live_status":status,
            "matched_identifiers":matched,
        })

    stock_rows=[]
    for item in verified:
        vid = mod.s(item["duplicate"].get("variant_id"))
        for wid in warehouse_ids:
            stock_rows.append({
                "variant_id":vid,
                "warehouse_id":wid,
                "quantity":0,
            })

    stock_errors=[]
    if stock_rows:
        try:
            stale = kit.bulk_stocks(stock_rows)
            if stale:
                stock_errors.append({"stale_variant_ids":stale})
        except Exception as exc:
            stock_errors.append({"error":str(exc)[:1200]})

    archived=[]
    archive_errors=[]
    if not stock_errors:
        for idx,item in enumerate(verified,1):
            dup=item["duplicate"]
            vid=mod.s(dup.get("variant_id"))
            try:
                kit.patch_variant(vid, {"status":"ARCHIVED"})
                archived.append({
                    "article":item["article"],
                    "sku":mod.s(dup.get("sku")),
                    "kit_id":dup.get("kit_id"),
                    "variant_id":vid,
                    "kept_sku":mod.s((item.get("kept") or {}).get("sku")),
                    "kept_kit_id":(item.get("kept") or {}).get("kit_id"),
                })
            except Exception as exc:
                archive_errors.append({
                    "article":item["article"],
                    "sku":mod.s(dup.get("sku")),
                    "kit_id":dup.get("kit_id"),
                    "variant_id":vid,
                    "error":str(exc)[:1000],
                })
            if idx % 100 == 0:
                print(f"Archived Norden chairs/stools duplicates: {idx}/{len(verified)}", flush=True)

    report={
        "started_at":datetime.now(timezone.utc).isoformat(),
        "scope":"Norden chairs/stools duplicates confirmed by read-only audit",
        "source":source_kind,
        "source_api_error":api_error,
        "planned_duplicates":len(planned),
        "verified_live_duplicates":len(verified),
        "skipped_after_live_recheck":len(skipped),
        "stock_zero_rows":len(stock_rows),
        "archived":len(archived),
        "archive_errors":archive_errors,
        "stock_errors":stock_errors,
        "skipped":skipped,
        "archived_items":archived,
        "hidden_fallback_used":False,
        "complete":not stock_errors and not archive_errors,
        "finished_at":datetime.now(timezone.utc).isoformat(),
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
        "planned_duplicates":report["planned_duplicates"],
        "verified_live_duplicates":report["verified_live_duplicates"],
        "archived":report["archived"],
        "archive_errors":len(report["archive_errors"]),
        "skipped_after_live_recheck":report["skipped_after_live_recheck"],
        "hidden_fallback_used":report["hidden_fallback_used"],
        "complete":report["complete"],
    },ensure_ascii=False,indent=2))
    if stock_errors or archive_errors:
        raise SystemExit(1)

if __name__=="__main__":
    main()
