#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
SYNC_PATH = HERE / "sync_treez_kit.py"
REPORT_PATH = HERE / "item_image_repair_report.json"


def load_sync():
    spec = importlib.util.spec_from_file_location("treez_sync_item", SYNC_PATH)
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
    code = (os.getenv("TREEZ_SOURCE_CODE") or "10.11957N").strip()
    token = m.s(os.getenv("YANDEX_KIT_TOKEN"))
    if not token:
        raise RuntimeError("YANDEX_KIT_TOKEN is not configured")

    raw = requests.get(
        m.SOURCE_XML_URL,
        timeout=180,
        headers={"User-Agent": "Mozilla/5.0 Treez-Item-Image-Repair"},
    )
    raw.raise_for_status()
    offers, _, _ = m.parse_feed(raw.content.decode("utf-8-sig", errors="replace"))
    item = offers.get(code)
    if not item:
        raise RuntimeError(f"Артикул {code} не найден в источнике")

    gallery = m.gallery_images(item)
    if not gallery:
        raise RuntimeError(f"Для {code} не найдены изображения галереи")

    kit = m.Kit(token)
    chars = kit.list_all("/v1/characteristics", {"status": "ACTIVE"}, "characteristics")
    titles = {m.s(x.get("id")): m.s(x.get("title")) for x in chars if m.s(x.get("id"))}

    rows = kit.list_all("/v1/variants", {"name": m.BRAND}, "variants")
    target = None
    for row in rows:
        if m.norm(row.get("brand")) != m.norm(m.BRAND):
            continue
        detail = row
        found = m.find_source_code(detail, titles)
        if not found and not (detail.get("characteristics") or []):
            detail = kit.get_variant(m.s(row.get("id")))
            found = m.find_source_code(detail, titles)
        if found == code:
            target = detail
            break

    if not target:
        raise RuntimeError(f"Карточка TREEZ {code} не найдена в KIT")

    vid = m.s(target.get("id"))
    detail = target if "media" in target else kit.get_variant(vid)
    before_media = list(detail.get("media") or [])
    before_images = sum(1 for x in before_media if m.s(x.get("type")).upper() == "IMAGE")

    report = {
        "status": "running",
        "source_code": code,
        "variant_id": vid,
        "kit_sku": m.s(detail.get("sku")),
        "xml_images": len(item.get("pictures") or []),
        "gallery_images": len(gallery),
        "kit_images_before": before_images,
        "images_uploaded": 0,
        "gallery_images_found": 0,
        "image_errors": 0,
        "warnings": [],
    }

    fresh_images = m.build_media(kit, dict(item, pictures=gallery), report)
    if len(fresh_images) != len(gallery):
        raise RuntimeError(
            f"Загружено {len(fresh_images)} из {len(gallery)} изображений"
        )

    preserved = [
        x for x in before_media
        if m.s(x.get("type")).upper() != "IMAGE"
    ]
    kit.patch_variant(vid, {"media": fresh_images + preserved})
    verify = kit.get_variant(vid)
    after_images = sum(
        1 for x in (verify.get("media") or [])
        if m.s(x.get("type")).upper() == "IMAGE"
    )

    report["kit_images_after"] = after_images
    report["status"] = "ok" if after_images >= len(gallery) else "partial"
    report["complete"] = after_images >= len(gallery)
    write_report(report)
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
