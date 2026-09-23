#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE.parent / "aletan-kit" / "sync_aletan_kit.py"
REPORT_PATH = HERE / "supplier_cleanup_report.json"
BRAND = "TREEZ"


def load_base():
    spec = importlib.util.spec_from_file_location("treez_cleanup_base", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {BASE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


base = load_base()


def s(v):
    return str(v or "").strip()


def norm(v):
    return " ".join(s(v).casefold().replace("ё", "е").split())


def write_report(report):
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main():
    token = s(os.getenv("YANDEX_KIT_TOKEN"))
    if not token:
        raise RuntimeError("YANDEX_KIT_TOKEN is not configured")

    kit = base.KitClient(token)

    characteristics = kit.list_all("/v1/characteristics", {"status": "ACTIVE"}, "characteristics")
    titles = {s(x.get("id")): s(x.get("title")) for x in characteristics if s(x.get("id"))}

    supplier_ids = {
        cid for cid, title in titles.items()
        if norm(title) == "поставщик"
    }

    report = {
        "status": "running",
        "brand": BRAND,
        "supplier_characteristic_ids": sorted(supplier_ids),
        "checked": 0,
        "cleaned": 0,
        "unchanged": 0,
        "errors": [],
    }

    if not supplier_ids:
        report["status"] = "ok"
        report["reason"] = "Характеристика «Поставщик» в KIT не найдена"
        write_report(report)
        return 0

    rows = kit.list_all("/v1/variants", {"name": BRAND}, "variants")
    treez_rows = [
        row for row in rows
        if norm(row.get("brand")) == norm(BRAND)
        and s(row.get("status")).upper() != "ARCHIVED"
        and s(row.get("id"))
    ]

    for row in treez_rows:
        vid = s(row.get("id"))
        report["checked"] += 1
        try:
            detail = row
            if "characteristics" not in detail:
                detail = kit.get_variant(vid)
            chars = list(detail.get("characteristics") or [])
            filtered = [
                ch for ch in chars
                if s(ch.get("characteristic_id")) not in supplier_ids
                and norm(ch.get("title")) != "поставщик"
            ]
            if filtered == chars:
                report["unchanged"] += 1
                continue
            kit.request("PATCH", f"/v1/variants/{vid}", body={"characteristics": filtered}, timeout=180)
            report["cleaned"] += 1
        except Exception as exc:
            report["errors"].append({
                "variant_id": vid,
                "sku": s(row.get("sku")),
                "message": str(exc)[:500],
            })

    report["status"] = "ok" if not report["errors"] else "partial"
    report["complete"] = not report["errors"]
    write_report(report)
    return 0 if not report["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
