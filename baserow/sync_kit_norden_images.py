#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

KIT_BASE = "https://api.kit.yandex.net"
BASEROW_URL = os.environ.get("BASEROW_URL", "http://147.78.67.6").rstrip("/")
BASEROW_TABLE_ID = 156
BRAND = "Norden"
REPORT = Path("baserow/last_kit_norden_images_sync.json")
MAPPING = Path("norden-kit/kit_mapping.json")

KIT_TOKEN = os.environ.get("YANDEX_KIT_TOKEN", "").strip()
BASEROW_TOKEN = os.environ.get("BASEROW_DATABASE_TOKEN", "").strip()

def s(v):
    return str(v or "").strip()

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def supplier_is_norden(row):
    value = row.get("Поставщик")
    if not value:
        return True
    if isinstance(value, list):
        return any(
            isinstance(x, dict) and s(x.get("value")).casefold() == BRAND.casefold()
            for x in value
        )
    return s(value).casefold() == BRAND.casefold()

class KitClient:
    def __init__(self, token):
        if not token:
            raise RuntimeError("YANDEX_KIT_TOKEN is missing")
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self.session = requests.Session()
        self._lock = threading.Lock()
        self._last = 0.0

    def _pace(self, delay=0.46):
        with self._lock:
            wait = delay - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def request(self, method, path, params=None, timeout=90, paced=True):
        last = None
        for attempt in range(12):
            if paced:
                self._pace()
            try:
                r = self.session.request(
                    method, KIT_BASE + path, headers=self.headers,
                    params=params, timeout=timeout
                )
            except requests.RequestException as exc:
                last = exc
                time.sleep(min(20, 2 ** attempt))
                continue
            if r.status_code == 429:
                last = RuntimeError("KIT rate limited")
                time.sleep(float(r.headers.get("Retry-After") or min(45, 3 + 2 * attempt)))
                continue
            if r.status_code >= 500:
                last = RuntimeError(f"KIT HTTP {r.status_code}")
                time.sleep(min(20, 2 ** attempt))
                continue
            if not r.ok:
                raise RuntimeError(f"KIT {method} {path} -> HTTP {r.status_code}: {r.text[:800]}")
            return r.json() if r.content else {}
        raise RuntimeError(f"KIT retries exhausted for {path}: {last}")

    @staticmethod
    def items(payload):
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("items", "results", "variants"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        data = payload.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return [x for x in data["items"] if isinstance(x, dict)]
        return []

    def get_variant(self, variant_id):
        return self.request("GET", f"/v1/variants/{variant_id}")

    def find_variant_by_sku(self, sku):
        payload = self.request(
            "GET", "/v1/variants",
            params={"name": sku, "page": 1, "per_page": 100}
        )
        rows = [
            x for x in self.items(payload)
            if s(x.get("sku")) == sku and s(x.get("brand")).casefold() == BRAND.casefold()
        ]
        if not rows:
            return None, None
        published = [x for x in rows if s(x.get("status")).upper() == "PUBLISHED"]
        candidates = published or rows
        if len(candidates) != 1:
            return None, f"{len(candidates)} exact KIT variants"
        return candidates[0], None

    def file_url(self, file_id):
        payload = self.request("GET", f"/v1/files/{file_id}", paced=False)
        url = s(payload.get("url")) if isinstance(payload, dict) else ""
        if not url:
            raise RuntimeError(f"KIT file {file_id} has no url")
        return url

class BaserowClient:
    def __init__(self, base, token):
        if not token:
            raise RuntimeError("BASEROW_DATABASE_TOKEN is missing")
        self.base = base
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def request(self, method, path, **kwargs):
        last = None
        for attempt in range(10):
            try:
                r = self.session.request(method, self.base + path, timeout=60, **kwargs)
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

    def rows(self):
        by_id = {}
        page = 1
        expected = None
        while True:
            data = self.request(
                "GET",
                f"/api/database/rows/table/{BASEROW_TABLE_ID}/?user_field_names=true&size=200&page={page}",
            )
            if expected is None:
                expected = data.get("count")
            for row in data.get("results", []):
                if isinstance(row, dict) and row.get("id") is not None:
                    by_id[row["id"]] = row
            if expected is not None and len(by_id) >= int(expected):
                break
            if not data.get("next"):
                break
            page += 1
            if page > 500:
                raise RuntimeError("Baserow pagination exceeded 500 pages")
        return list(by_id.values()), expected

    def update(self, row_id, data):
        return self.request(
            "PATCH",
            f"/api/database/rows/table/{BASEROW_TABLE_ID}/{row_id}/?user_field_names=true",
            data=json.dumps(data, ensure_ascii=False),
        )

    def upload_via_url(self, url):
        return self.request(
            "POST",
            "/api/user-files/upload-via-url/",
            data=json.dumps({"url": url}, ensure_ascii=False),
        )

def mapping_index():
    by_sku = defaultdict(list)
    if not MAPPING.exists():
        return by_sku
    try:
        data = json.loads(MAPPING.read_text(encoding="utf-8"))
    except Exception:
        return by_sku
    variants = data.get("variants") if isinstance(data, dict) else None
    if not isinstance(variants, dict):
        return by_sku
    for key, rows in variants.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            vid = s(row.get("variant_id"))
            sku = s(row.get("sku"))
            if vid and sku:
                by_sku[sku].append(vid)
            if vid and s(key).startswith("AF-"):
                by_sku[s(key)].append(vid)
    return by_sku

def media_ids(variant):
    media = [
        x for x in (variant.get("media") or [])
        if isinstance(x, dict)
        and s(x.get("type")).upper() == "IMAGE"
        and s(x.get("image_id"))
    ]
    media.sort(key=lambda x: (
        x.get("display_sequence") is None,
        x.get("display_sequence") if x.get("display_sequence") is not None else 0
    ))
    return list(dict.fromkeys(s(x.get("image_id")) for x in media))

def main():
    report = {
        "started_at": now_iso(),
        "brand": BRAND,
        "baserow_api_count": None,
        "baserow_unique_rows": 0,
        "baserow_norden_rows": 0,
        "kit_matched": 0,
        "kit_missing": 0,
        "kit_ambiguous": 0,
        "rows_with_images": 0,
        "rows_without_images": 0,
        "updated_rows": 0,
        "unchanged_rows": 0,
        "uploaded_first_images": 0,
        "errors": [],
        "complete": False,
    }

    br = BaserowClient(BASEROW_URL, BASEROW_TOKEN)
    kit = KitClient(KIT_TOKEN)

    all_rows, expected = br.rows()
    report["baserow_api_count"] = expected
    report["baserow_unique_rows"] = len(all_rows)

    target_rows = [
        row for row in all_rows
        if s(row.get("Артикул")) and supplier_is_norden(row)
    ]
    report["baserow_norden_rows"] = len(target_rows)

    rows_by_sku = defaultdict(list)
    for row in target_rows:
        rows_by_sku[s(row.get("Артикул"))].append(row)

    map_idx = mapping_index()

    variants = {}
    missing = []
    ambiguous = []

    for idx, sku in enumerate(sorted(rows_by_sku), 1):
        chosen = None
        mapped_ids = list(dict.fromkeys(map_idx.get(sku, [])))
        if len(mapped_ids) == 1:
            try:
                candidate = kit.get_variant(mapped_ids[0])
                if s(candidate.get("sku")) == sku and s(candidate.get("brand")).casefold() == BRAND.casefold():
                    chosen = candidate
            except Exception:
                chosen = None
        if chosen is None:
            chosen, reason = kit.find_variant_by_sku(sku)
            if reason:
                ambiguous.append((sku, reason))
                continue
        if chosen is None:
            missing.append(sku)
            continue
        variants[sku] = chosen
        if idx % 100 == 0:
            print(f"KIT product lookup {idx}/{len(rows_by_sku)} matched={len(variants)}", flush=True)

    report["kit_matched"] = len(variants)
    report["kit_missing"] = len(missing)
    report["kit_ambiguous"] = len(ambiguous)

    all_file_ids = set()
    ids_by_sku = {}
    for sku, variant in variants.items():
        ids = media_ids(variant)
        ids_by_sku[sku] = ids
        all_file_ids.update(ids)

    url_by_id = {}
    def resolve(fid):
        local = KitClient(KIT_TOKEN)
        return fid, local.file_url(fid)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(resolve, fid): fid for fid in all_file_ids}
        done = 0
        for fut in as_completed(futures):
            fid = futures[fut]
            try:
                key, url = fut.result()
                url_by_id[key] = url
            except Exception as exc:
                report["errors"].append({
                    "stage": "kit_file_url",
                    "file_id": fid,
                    "message": str(exc)[:700],
                })
            done += 1
            if done % 250 == 0 or done == len(all_file_ids):
                print(f"KIT image URLs {done}/{len(all_file_ids)}", flush=True)

    for n, (sku, rows) in enumerate(rows_by_sku.items(), 1):
        if sku not in variants:
            continue
        urls = [url_by_id[x] for x in ids_by_sku.get(sku, []) if x in url_by_id]
        urls = list(dict.fromkeys(urls))
        first_url = urls[0] if urls else ""
        all_urls = "\n".join(urls)

        for row in rows:
            current_first_url = s(row.get("Первое изображение URL"))
            current_all = s(row.get("Все изображения"))
            current_file = row.get("Первое изображение") or []

            body = {}
            if current_all != all_urls:
                body["Все изображения"] = all_urls
            if current_first_url != first_url:
                body["Первое изображение URL"] = first_url

            if first_url:
                report["rows_with_images"] += 1
                if current_first_url != first_url or not current_file:
                    try:
                        uploaded = br.upload_via_url(first_url)
                        name = s(uploaded.get("name"))
                        if not name:
                            raise RuntimeError("Baserow upload returned no file name")
                        body["Первое изображение"] = [
                            {"name": name, "visible_name": f"{sku}.jpg"}
                        ]
                        report["uploaded_first_images"] += 1
                    except Exception as exc:
                        report["errors"].append({
                            "stage": "baserow_upload_first_image",
                            "sku": sku,
                            "message": str(exc)[:700],
                        })
            else:
                report["rows_without_images"] += 1
                if current_file:
                    body["Первое изображение"] = []

            if body:
                try:
                    br.update(row["id"], body)
                    report["updated_rows"] += 1
                except Exception as exc:
                    report["errors"].append({
                        "stage": "baserow_update",
                        "sku": sku,
                        "row_id": row.get("id"),
                        "message": str(exc)[:700],
                    })
            else:
                report["unchanged_rows"] += 1

        if n % 100 == 0:
            print(
                f"Baserow sync {n}/{len(rows_by_sku)} updated={report['updated_rows']} "
                f"uploaded_previews={report['uploaded_first_images']}",
                flush=True,
            )

    report["missing_sample"] = missing[:100]
    report["ambiguous_sample"] = ambiguous[:100]
    report["finished_at"] = now_iso()
    report["complete"] = len(report["errors"]) == 0 and len(ambiguous) == 0

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["complete"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
