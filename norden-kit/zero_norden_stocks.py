#!/usr/bin/env python3
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "zero_norden_stocks_report.json"


def s(v):
    return str(v or "").strip()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_module():
    path = ROOT / "sync_norden_kit.py"
    spec = importlib.util.spec_from_file_location("norden_sync", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    mod = load_module()
    kit = mod.KitClient(os.environ.get("YANDEX_KIT_TOKEN", ""))
    report = {
        "started_at": now_iso(),
        "brand": "Norden",
        "variants_scanned": 0,
        "active_norden_variants": 0,
        "warehouses": [],
        "stock_rows_zeroed": 0,
        "errors": [],
        "sample": [],
        "complete": False,
    }

    warehouses = [
        x for x in kit.warehouses()
        if s(x.get("id")) and s(x.get("status")).upper() != "ARCHIVED"
    ]
    if not warehouses:
        raise RuntimeError("No KIT warehouses found")
    report["warehouses"] = [
        {"id": s(x.get("id")), "title": s(x.get("title"))}
        for x in warehouses
    ]

    norden = []
    page = 1
    while True:
        payload = kit.request("GET", "/v1/variants", params={"page": page, "per_page": 100})
        rows = kit.items(payload)
        if not rows:
            break
        report["variants_scanned"] += len(rows)
        for row in rows:
            if s(row.get("brand")).casefold() != "norden":
                continue
            if s(row.get("status")).upper() == "ARCHIVED":
                continue
            vid = s(row.get("id"))
            if not vid:
                continue
            norden.append(row)
            if len(report["sample"]) < 50:
                report["sample"].append({
                    "variant_id": vid,
                    "kit_id": row.get("kit_id"),
                    "sku": s(row.get("sku")),
                    "name": s(row.get("name")),
                })
        total = payload.get("total_count") or payload.get("total")
        if len(rows) < 100 or (total not in (None, "") and page * 100 >= int(total)):
            break
        page += 1
        if page % 50 == 0:
            print(f"Scanned {report['variants_scanned']} KIT variants; Norden found {len(norden)}", flush=True)

    report["active_norden_variants"] = len(norden)
    if len(norden) < 100:
        raise RuntimeError(f"Safety stop: only {len(norden)} active Norden variants found")

    stock_rows = []
    for row in norden:
        vid = s(row.get("id"))
        for wh in warehouses:
            stock_rows.append({
                "variant_id": vid,
                "warehouse_id": s(wh.get("id")),
                "quantity": 0,
            })

    try:
        kit.bulk_stocks(stock_rows)
        report["stock_rows_zeroed"] = len(stock_rows)
    except Exception as exc:
        report["errors"].append({"stage": "bulk_zero", "message": str(exc)[:1000]})

    # Verify a representative sample directly.
    for row in norden[:25]:
        try:
            current = kit.get_variant(s(row.get("id")))
            bad = [
                x for x in (current.get("stocks") or [])
                if int(float(str(x.get("quantity") or 0))) != 0
            ]
            if bad:
                report["errors"].append({
                    "stage": "verify",
                    "sku": s(row.get("sku")),
                    "message": f"non-zero stocks remain: {bad}",
                })
        except Exception as exc:
            report["errors"].append({
                "stage": "verify",
                "sku": s(row.get("sku")),
                "message": str(exc)[:700],
            })

    report["finished_at"] = now_iso()
    report["complete"] = not report["errors"]
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
