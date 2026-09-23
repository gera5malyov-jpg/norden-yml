#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import concurrent.futures
import json
import os
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
SYNC_PATH = HERE / "sync_treez_kit.py"
REPORT_PATH = HERE / "image_repair_report.json"


def load_sync():
    spec = importlib.util.spec_from_file_location("treez_sync", SYNC_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {SYNC_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m = load_sync()


def write_report(report):
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main():
    token = m.s(os.getenv("YANDEX_KIT_TOKEN"))
    if not token:
        raise RuntimeError("YANDEX_KIT_TOKEN is not configured")

    raw = requests.get(
        m.SOURCE_XML_URL,
        timeout=180,
        headers={"User-Agent": "Mozilla/5.0 Treez-GitHub-Image-Repair"},
    )
    raw.raise_for_status()
    offers, _, duplicates = m.parse_feed(raw.content.decode("utf-8-sig", errors="replace"))

    kit = m.Kit(token)

    chars = kit.list_all("/v1/characteristics", {"status": "ACTIVE"}, "characteristics")
    titles = {m.s(x.get("id")): m.s(x.get("title")) for x in chars if m.s(x.get("id"))}

    rows = kit.list_all("/v1/variants", {"name": m.BRAND}, "variants")
    by_source = {}
    for row in rows:
        if m.norm(row.get("brand")) != m.norm(m.BRAND):
            continue
        if m.s(row.get("status")).upper() == "ARCHIVED":
            continue
        detail = row
        code = m.find_source_code(detail, titles)
        if not code and not (detail.get("characteristics") or []):
            try:
                detail = kit.get_variant(m.s(row.get("id")))
                code = m.find_source_code(detail, titles)
            except Exception:
                continue
        if code and code not in by_source:
            by_source[code] = detail

    report = {
        "status": "running",
        "gallery_fetch_workers": 12,
        "source_offers": len(offers),
        "source_duplicates": len(duplicates),
        "kit_treez_indexed": len(by_source),
        "checked": 0,
        "gallery_bigger_than_xml": 0,
        "already_complete": 0,
        "repaired": 0,
        "images_uploaded": 0,
        "gallery_images_found": 0,
        "image_errors": 0,
        "missing_in_kit": 0,
        "warnings": [],
        "errors": [],
        "examples": [],
    }

    gallery_by_code = {}
    def fetch_gallery(pair):
        code, item = pair
        try:
            return code, m.gallery_images(item), None
        except Exception as exc:
            return code, [], str(exc)[:700]

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        for code, gallery, err in ex.map(fetch_gallery, offers.items()):
            gallery_by_code[code] = gallery
            if err:
                report["warnings"].append({
                    "source_code": code,
                    "stage": "gallery_fetch",
                    "message": err,
                })

    for idx, (code, item) in enumerate(offers.items(), start=1):
        variant = by_source.get(code)
        if not variant:
            report["missing_in_kit"] += 1
            continue

        report["checked"] += 1

        try:
            gallery = gallery_by_code.get(code) or list(item.get("pictures") or [])
            xml_count = len(item.get("pictures") or [])
            gallery_count = len(gallery)

            if gallery_count > xml_count:
                report["gallery_bigger_than_xml"] += 1

            vid = m.s(variant.get("id"))
            detail = variant if "media" in variant else kit.get_variant(vid)
            current_media = list(detail.get("media") or [])
            current_image_count = sum(
                1 for row in current_media
                if m.s(row.get("type")).upper() == "IMAGE"
            )

            if len(report["examples"]) < 100 and (
                gallery_count > xml_count or gallery_count > current_image_count
            ):
                report["examples"].append({
                    "source_code": code,
                    "xml_images": xml_count,
                    "gallery_images": gallery_count,
                    "kit_images_before": current_image_count,
                })

            if gallery_count <= current_image_count:
                report["already_complete"] += 1
                continue

            fresh_images = m.build_media(kit, dict(item, pictures=gallery), report)
            if not fresh_images:
                raise RuntimeError("Не удалось загрузить изображения галереи")

            preserved = [
                row for row in current_media
                if m.s(row.get("type")).upper() != "IMAGE"
            ]
            kit.patch_variant(vid, {"media": fresh_images + preserved})
            report["repaired"] += 1

        except Exception as exc:
            report["errors"].append({
                "source_code": code,
                "variant_id": m.s(variant.get("id")),
                "message": str(exc)[:700],
            })

        if idx % 50 == 0:
            print(f"Проверено источников: {idx}/{len(offers)}", flush=True)

        time.sleep(0.05)

    report["status"] = "ok" if not report["errors"] else "partial"
    report["complete"] = not report["errors"]
    write_report(report)
    return 0 if not report["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
