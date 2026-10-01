#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "liga-kit"))

from liga_kit.http import SafeSession
from liga_kit.kit_client import KitClient

BASEROW_URL = os.environ.get("BASEROW_URL", "http://147.78.67.6").rstrip("/")
BASEROW_TOKEN = os.environ.get("BASEROW_DATABASE_TOKEN", "").strip()
KIT_TOKEN = os.environ.get("YANDEX_KIT_TOKEN", "").strip()
CATALOG_TABLE_ID = 156
SUPPLIERS_TABLE_ID = 157
SUPPLIER_NAME = "Лига диванов"
REPORT = ROOT / "baserow" / "last_kit_liga_images_sync.json"


def s(v):
    return str(v or "").strip()


def norm(v):
    return unicodedata.normalize("NFKC", s(v)).casefold()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class Baserow:
    def __init__(self):
        if not BASEROW_TOKEN:
            raise RuntimeError("BASEROW_DATABASE_TOKEN is missing")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token {BASEROW_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def request(self, method, path, **kwargs):
        last = None
        for attempt in range(7):
            try:
                r = self.session.request(method, BASEROW_URL + path, timeout=90, **kwargs)
            except requests.RequestException as exc:
                last = exc
                time.sleep(min(15, 2 ** attempt))
                continue
            if r.status_code >= 500:
                last = RuntimeError(f"Baserow HTTP {r.status_code}")
                time.sleep(min(15, 2 ** attempt))
                continue
            if not r.ok:
                raise RuntimeError(f"Baserow {method} {path} -> HTTP {r.status_code}: {r.text[:1000]}")
            return r.json() if r.content else {}
        raise RuntimeError(f"Baserow retries exhausted: {last}")

    def rows(self, table_id):
        out = []
        page = 1
        while True:
            data = self.request(
                "GET",
                f"/api/database/rows/table/{table_id}/?user_field_names=true&size=200&page={page}",
            )
            out.extend(x for x in data.get("results", []) if isinstance(x, dict))
            if not data.get("next"):
                break
            page += 1
        return out

    def update(self, row_id, body):
        return self.request(
            "PATCH",
            f"/api/database/rows/table/{CATALOG_TABLE_ID}/{row_id}/?user_field_names=true",
            data=json.dumps(body, ensure_ascii=False),
        )

    def upload_via_url(self, url):
        return self.request(
            "POST",
            "/api/user-files/upload-via-url/",
            data=json.dumps({"url": url}, ensure_ascii=False),
        )


def supplier_ids(row):
    out = set()
    for x in row.get("Поставщик") or []:
        if isinstance(x, dict) and x.get("id") is not None:
            try:
                out.add(int(x["id"]))
            except Exception:
                pass
    return out


def media_ids(variant):
    rows = [
        x for x in (variant.get("media") or [])
        if isinstance(x, dict)
        and s(x.get("type")).upper() == "IMAGE"
        and s(x.get("image_id"))
    ]
    rows.sort(key=lambda x: int(x.get("display_sequence") or 0))
    return list(dict.fromkeys(s(x.get("image_id")) for x in rows))


def main():
    if not KIT_TOKEN:
        raise RuntimeError("YANDEX_KIT_TOKEN is missing")

    report = {
        "started_at": now_iso(),
        "supplier": SUPPLIER_NAME,
        "database_rows": 0,
        "kit_variants": 0,
        "matched": 0,
        "kit_ids_written": 0,
        "image_links_written": 0,
        "previews_written": 0,
        "missing_in_kit": [],
        "errors": [],
        "complete": False,
    }

    br = Baserow()
    suppliers = br.rows(SUPPLIERS_TABLE_ID)
    matches = [r for r in suppliers if norm(r.get("Поставщик")) == norm(SUPPLIER_NAME)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one supplier {SUPPLIER_NAME!r}, found {len(matches)}")
    supplier_id = int(matches[0]["id"])

    rows = [r for r in br.rows(CATALOG_TABLE_ID) if supplier_id in supplier_ids(r)]
    report["database_rows"] = len(rows)

    http = SafeSession()
    kit = KitClient(KIT_TOKEN, http)
    index, duplicates = kit.index_liga_variants()
    report["kit_variants"] = len(index)
    if duplicates:
        report["errors"].append({
            "stage": "kit_duplicates",
            "count": len(duplicates),
            "sample": list(duplicates)[:50],
        })

    file_url_cache = {}

    def file_url(fid):
        if fid not in file_url_cache:
            payload = kit._get(f"/v1/files/{fid}")
            file_url_cache[fid] = s(payload.get("url"))
        return file_url_cache[fid]

    for row in rows:
        sku = s(row.get("Артикул"))
        vendor = s(row.get("Артикул поставщика") or row.get("Наименование артикула"))
        if not sku and vendor:
            sku = "liga-" + vendor
        variant = index.get(sku)
        if variant is None:
            report["missing_in_kit"].append(sku or vendor)
            continue

        try:
            full = kit.get_variant(s(variant.get("id")))
            kit_id = full.get("kit_id")
            if kit_id in (None, ""):
                raise RuntimeError("KIT variant has no kit_id")

            urls = []
            for fid in media_ids(full):
                url = file_url(fid)
                if url and url not in urls:
                    urls.append(url)

            body = {}
            if s(row.get("Артикул KIT")) != s(kit_id):
                body["Артикул KIT"] = s(kit_id)
                report["kit_ids_written"] += 1

            all_urls = "\n".join(urls)
            first_url = urls[0] if urls else ""
            if s(row.get("Все изображения")) != all_urls:
                body["Все изображения"] = all_urls
            if s(row.get("Первое изображение URL")) != first_url:
                body["Первое изображение URL"] = first_url
            if urls and (
                s(row.get("Все изображения")) != all_urls
                or s(row.get("Первое изображение URL")) != first_url
            ):
                report["image_links_written"] += 1

            current_file = row.get("Первое изображение") or []
            if first_url and (not current_file or s(row.get("Первое изображение URL")) != first_url):
                uploaded = br.upload_via_url(first_url)
                name = s(uploaded.get("name"))
                if name:
                    body["Первое изображение"] = [{
                        "name": name,
                        "visible_name": f"{sku}.jpg",
                    }]
                    report["previews_written"] += 1

            if body:
                br.update(row["id"], body)
            report["matched"] += 1
        except Exception as exc:
            report["errors"].append({
                "stage": "row",
                "sku": sku,
                "row_id": row.get("id"),
                "message": str(exc)[:800],
            })

    report["finished_at"] = now_iso()
    report["complete"] = not report["errors"] and not report["missing_in_kit"]
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
