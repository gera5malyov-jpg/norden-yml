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

KIT_TOKEN = os.environ.get("YANDEX_KIT_TOKEN", "").strip()
BASEROW_TOKEN = os.environ.get("BASEROW_DATABASE_TOKEN", "").strip()

def s(v):
    return str(v or "").strip()

def now_iso():
    return datetime.now(timezone.utc).isoformat()

class KitClient:
    def __init__(self, token):
        if not token:
            raise RuntimeError("YANDEX_KIT_TOKEN is missing")
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self.session = requests.Session()
        self._lock = threading.Lock()
        self._last = 0.0

    def _pace(self, delay=0.42):
        with self._lock:
            wait = delay - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def request(self, method, path, params=None, timeout=90, paced=True):
        last = None
        for attempt in range(10):
            if paced:
                self._pace()
            try:
                r = self.session.request(
                    method,
                    KIT_BASE + path,
                    headers=self.headers,
                    params=params,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                last = exc
                time.sleep(min(20, 2 ** attempt))
                continue
            if r.status_code == 429:
                last = RuntimeError("KIT rate limited")
                time.sleep(float(r.headers.get("Retry-After") or min(30, 2 + attempt * 2)))
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

    @staticmethod
    def total(payload):
        if not isinstance(payload, dict):
            return None
        for key in ("total", "total_count"):
            if isinstance(payload.get(key), int):
                return payload[key]
        meta = payload.get("meta")
        if isinstance(meta, dict):
            for key in ("total", "total_count"):
                if isinstance(meta.get(key), int):
                    return meta[key]
        return None

    def variants_for_skus(self, wanted):
        found = defaultdict(list)
        page = 1
        seen = 0
        while True:
            payload = self.request("GET", "/v1/variants", params={"page": page, "per_page": 100})
            rows = self.items(payload)
            if not rows:
                break
            for row in rows:
                seen += 1
                sku = s(row.get("sku"))
                if sku in wanted and s(row.get("brand")).casefold() == BRAND.casefold():
                    found[sku].append(row)
            total = self.total(payload)
            if page % 25 == 0:
                print(f"KIT scan page={page}, seen={seen}, matched_skus={len(found)}", flush=True)
            if (total is not None and seen >= total) or (total is None and len(rows) < 100):
                break
            page += 1
        print(f"KIT scan complete: pages={page}, seen={seen}, matched_skus={len(found)}", flush=True)
        return found

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
        for attempt in range(8):
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
                raise RuntimeError(f"Baserow {method} {path} -> HTTP {r.status_code}: {r.text[:800]}")
            return r.json() if r.content else {}
        raise RuntimeError(f"Baserow retries exhausted: {last}")

    def rows(self):
        out = []
        page = 1
        while True:
            data = self.request(
                "GET",
                f"/api/database/rows/table/{BASEROW_TABLE_ID}/?user_field_names=true&size=200&page={page}",
            )
            out.extend(data.get("results", []))
            if not data.get("next"):
                break
            page += 1
        return out

    def update(self, row_id, data):
        return self.request(
            "PATCH",
            f"/api/database/rows/table/{BASEROW_TABLE_ID}/{row_id}/?user_field_names=true",
            data=json.dumps(data, ensure_ascii=False),
        )

def choose_variant(rows):
    if not rows:
        return None, None
    published = [r for r in rows if s(r.get("status")).upper() == "PUBLISHED"]
    if len(published) == 1:
        return published[0], None
    if len(published) > 1:
        return None, f"multiple PUBLISHED variants ({len(published)})"
    active = [r for r in rows if s(r.get("status")).upper() != "ARCHIVED"]
    if len(active) == 1:
        return active[0], None
    if len(active) > 1:
        return None, f"multiple non-archived variants ({len(active)})"
    if len(rows) == 1:
        return rows[0], None
    return None, f"multiple archived/other variants ({len(rows)})"

def main():
    report = {
        "started_at": now_iso(),
        "brand": BRAND,
        "baserow_rows": 0,
        "kit_matched": 0,
        "kit_missing": 0,
        "kit_ambiguous": 0,
        "with_images": 0,
        "without_images": 0,
        "unique_image_ids": 0,
        "resolved_image_urls": 0,
        "updated_rows": 0,
        "unchanged_rows": 0,
        "errors": [],
        "ambiguous": [],
        "missing_sample": [],
        "complete": False,
    }

    br = BaserowClient(BASEROW_URL, BASEROW_TOKEN)
    kit = KitClient(KIT_TOKEN)

    rows = br.rows()
    report["baserow_rows"] = len(rows)
    by_sku = {}
    duplicates = []
    for row in rows:
        sku = s(row.get("Артикул"))
        if not sku:
            continue
        if sku in by_sku:
            duplicates.append(sku)
        by_sku[sku] = row
    if duplicates:
        raise RuntimeError(f"Duplicate Baserow articles: {duplicates[:20]}")

    wanted = set(by_sku)
    kit_rows = kit.variants_for_skus(wanted)

    selected = {}
    image_ids = set()
    for sku in sorted(wanted):
        rows_for_sku = kit_rows.get(sku, [])
        variant, reason = choose_variant(rows_for_sku)
        if reason:
            report["kit_ambiguous"] += 1
            if len(report["ambiguous"]) < 100:
                report["ambiguous"].append({"sku": sku, "reason": reason})
            continue
        if variant is None:
            report["kit_missing"] += 1
            if len(report["missing_sample"]) < 100:
                report["missing_sample"].append(sku)
            continue
        report["kit_matched"] += 1
        media = [
            m for m in (variant.get("media") or [])
            if isinstance(m, dict)
            and s(m.get("type")).upper() == "IMAGE"
            and s(m.get("image_id"))
        ]
        media.sort(key=lambda m: (m.get("display_sequence") is None, m.get("display_sequence") or 0))
        ids = [s(m.get("image_id")) for m in media]
        selected[sku] = ids
        image_ids.update(ids)

    report["unique_image_ids"] = len(image_ids)
    print(
        f"Matched KIT={report['kit_matched']}; missing={report['kit_missing']}; "
        f"ambiguous={report['kit_ambiguous']}; unique image ids={len(image_ids)}",
        flush=True,
    )

    url_by_id = {}
    def resolve(fid):
        local = KitClient(KIT_TOKEN)
        return fid, local.file_url(fid)

    if image_ids:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(resolve, fid): fid for fid in image_ids}
            done = 0
            for fut in as_completed(futures):
                fid = futures[fut]
                try:
                    key, url = fut.result()
                    url_by_id[key] = url
                except Exception as exc:
                    report["errors"].append({"file_id": fid, "stage": "resolve_file", "message": str(exc)[:700]})
                done += 1
                if done % 250 == 0 or done == len(image_ids):
                    print(f"Resolved KIT image URLs {done}/{len(image_ids)}", flush=True)

    report["resolved_image_urls"] = len(url_by_id)

    for index, (sku, ids) in enumerate(selected.items(), 1):
        row = by_sku[sku]
        urls = [url_by_id[fid] for fid in ids if fid in url_by_id]
        # Keep order and remove accidental duplicate URLs.
        urls = list(dict.fromkeys(urls))
        first = urls[0] if urls else ""
        all_images = "\n".join(urls)

        if urls:
            report["with_images"] += 1
        else:
            report["without_images"] += 1

        current_first = s(row.get("Первое изображение"))
        current_all = s(row.get("Все изображения"))
        if current_first == first and current_all == all_images:
            report["unchanged_rows"] += 1
            continue

        br.update(row["id"], {
            "Первое изображение": first,
            "Все изображения": all_images,
        })
        report["updated_rows"] += 1

        if index % 100 == 0:
            print(
                f"Baserow progress {index}/{len(selected)} updated={report['updated_rows']} unchanged={report['unchanged_rows']}",
                flush=True,
            )

    report["finished_at"] = now_iso()
    report["complete"] = report["kit_ambiguous"] == 0 and not report["errors"]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["complete"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
