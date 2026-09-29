#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "webasyst"))

from client import WebasystClient

TYPE_NAME = "NORDEN-100"
SUPPLIER_NAME = "Norden"
CATALOG_TABLE_ID = 156
SUPPLIERS_TABLE_ID = 157
REPORT_PATH = HERE / "last_webasyst_norden_to_baserow.json"

BASEROW_URL = os.environ.get("BASEROW_URL", "http://147.78.67.6").rstrip("/")
BASEROW_TOKEN = os.environ.get("BASEROW_DATABASE_TOKEN", "").strip()


def s(value):
    return str(value or "").strip()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def listify(payload, keys=()):
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            return [x for x in value.values() if isinstance(x, dict)]
    if payload and all(isinstance(v, dict) for v in payload.values()):
        return list(payload.values())
    return []


def product_skus(product):
    rows = product.get("skus")
    if isinstance(rows, dict):
        return [x for x in rows.values() if isinstance(x, dict)]
    if isinstance(rows, list):
        return [x for x in rows if isinstance(x, dict)]
    return []


class BaserowClient:
    def __init__(self, base_url, token):
        if not token:
            raise RuntimeError("BASEROW_DATABASE_TOKEN is missing")
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "norden-yml-webasyst-baserow-sync/1.0",
        })

    def request(self, method, path, **kwargs):
        url = self.base_url + path
        last = None
        for attempt in range(6):
            try:
                response = self.session.request(method, url, timeout=60, **kwargs)
                if response.status_code >= 500:
                    last = RuntimeError(f"Baserow HTTP {response.status_code}: {response.text[:500]}")
                    time.sleep(min(2 ** attempt, 20))
                    continue
                if not response.ok:
                    raise RuntimeError(
                        f"Baserow {method} {path} -> HTTP {response.status_code}: {response.text[:1000]}"
                    )
                if response.status_code == 204 or not response.text:
                    return None
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                last = exc
                time.sleep(min(2 ** attempt, 20))
        raise RuntimeError(f"Baserow request failed after retries: {last}")

    def wait_healthy(self, timeout_seconds=1200):
        deadline = time.time() + timeout_seconds
        last = None
        while time.time() < deadline:
            try:
                response = requests.get(self.base_url + "/api/_health/", timeout=10)
                if response.ok and response.text.strip() == "OK":
                    return
                last = f"HTTP {response.status_code} {response.text[:200]}"
            except Exception as exc:
                last = str(exc)
            time.sleep(5)
        raise RuntimeError(f"Baserow did not become healthy: {last}")

    def fields(self, table_id):
        return self.request("GET", f"/api/database/fields/table/{table_id}/") or []

    def all_rows(self, table_id, page_size=200):
        rows = []
        page = 1
        while True:
            payload = self.request(
                "GET",
                f"/api/database/rows/table/{table_id}/?user_field_names=true&size={page_size}&page={page}",
            )
            batch = payload.get("results", []) if isinstance(payload, dict) else []
            rows.extend(batch)
            if not payload.get("next"):
                break
            page += 1
        return rows

    def create_row(self, table_id, body):
        return self.request(
            "POST",
            f"/api/database/rows/table/{table_id}/?user_field_names=true",
            data=json.dumps(body, ensure_ascii=False),
        )

    def update_row(self, table_id, row_id, body):
        return self.request(
            "PATCH",
            f"/api/database/rows/table/{table_id}/{row_id}/?user_field_names=true",
            data=json.dumps(body, ensure_ascii=False),
        )


def load_webasyst_products(wa, type_id):
    out = []
    offset = 0
    while True:
        payload = wa.call(
            "shop.product.search",
            params={
                "hash": f"type/{type_id}",
                "offset": offset,
                "limit": 1000,
                "fields": "*,skus,stock_counts",
            },
        )
        batch = listify(payload, ("products", "items"))
        total = (
            payload.get("count") or payload.get("total_count")
            if isinstance(payload, dict)
            else None
        )
        out.extend(batch)
        if not batch or len(batch) < 1000:
            break
        if total not in (None, "") and len(out) >= int(total):
            break
        offset += len(batch)
    return out


def main():
    report = {
        "started_at": now_iso(),
        "source": "Webasyst",
        "webasyst_type": TYPE_NAME,
        "supplier": SUPPLIER_NAME,
        "webasyst_products": 0,
        "webasyst_skus": 0,
        "source_rows": 0,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "supplier_created": False,
        "supplier_row_id": None,
        "errors": [],
        "sample": [],
        "complete": False,
    }

    try:
        br = BaserowClient(BASEROW_URL, BASEROW_TOKEN)
        br.wait_healthy()

        catalog_fields = br.fields(CATALOG_TABLE_ID)
        catalog_field_names = {f.get("name") for f in catalog_fields}
        required = {"Название", "Артикул", "Наименование артикула", "Поставщик"}
        missing = sorted(required - catalog_field_names)
        if missing:
            raise RuntimeError(f"Baserow catalog missing fields: {missing}")

        supplier_fields = br.fields(SUPPLIERS_TABLE_ID)
        supplier_field_names = {f.get("name") for f in supplier_fields}
        if "Поставщик" not in supplier_field_names:
            raise RuntimeError("Baserow suppliers table has no field 'Поставщик'")

        wa = WebasystClient(
            base_url=os.environ.get("WEBASYST_BASE_URL", "https://profikompany.ru"),
            token=os.environ.get("WEBASYST_API_TOKEN", ""),
            min_request_interval=0.20,
        )

        types = listify(wa.call("shop.type.getList"))
        matches = [
            row
            for row in types
            if s(row.get("name") or row.get("title")).casefold() == TYPE_NAME.casefold()
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected exactly one Webasyst type {TYPE_NAME!r}, found {len(matches)}"
            )
        type_id = s(matches[0].get("id"))

        products = load_webasyst_products(wa, type_id)
        report["webasyst_products"] = len(products)

        entries = []
        for product in products:
            name = s(product.get("name"))
            for sku_row in product_skus(product):
                article = s(sku_row.get("sku"))
                if not article:
                    continue
                article_name = s(sku_row.get("name"))
                report["webasyst_skus"] += 1
                entries.append({
                    "Название": name,
                    "Артикул": article,
                    "Наименование артикула": article_name,
                })

        report["source_rows"] = len(entries)
        if not entries:
            raise RuntimeError("No NORDEN-100 SKU rows found in Webasyst")

        source_by_article = defaultdict(list)
        for entry in entries:
            source_by_article[entry["Артикул"]].append(entry)
        source_duplicates = {
            article: rows for article, rows in source_by_article.items() if len(rows) > 1
        }
        if source_duplicates:
            sample = list(source_duplicates)[:20]
            raise RuntimeError(
                f"Duplicate Webasyst SKU values found ({len(source_duplicates)}): {sample}"
            )

        supplier_rows = br.all_rows(SUPPLIERS_TABLE_ID)
        norden_rows = [
            row
            for row in supplier_rows
            if s(row.get("Поставщик")).casefold() == SUPPLIER_NAME.casefold()
        ]
        if len(norden_rows) > 1:
            raise RuntimeError(
                f"Multiple supplier rows named Norden in Baserow: {[r.get('id') for r in norden_rows]}"
            )
        if norden_rows:
            supplier_id = norden_rows[0]["id"]
        else:
            created_supplier = br.create_row(
                SUPPLIERS_TABLE_ID,
                {"Поставщик": SUPPLIER_NAME},
            )
            supplier_id = created_supplier["id"]
            report["supplier_created"] = True
        report["supplier_row_id"] = supplier_id

        catalog_rows = br.all_rows(CATALOG_TABLE_ID)
        target_by_article = defaultdict(list)
        for row in catalog_rows:
            article = s(row.get("Артикул"))
            if article:
                target_by_article[article].append(row)

        target_duplicates = {
            article: rows for article, rows in target_by_article.items() if len(rows) > 1
        }
        if target_duplicates:
            sample = list(target_duplicates)[:20]
            raise RuntimeError(
                f"Duplicate Baserow article values found ({len(target_duplicates)}): {sample}"
            )

        for entry in entries:
            article = entry["Артикул"]
            body = {
                "Название": entry["Название"],
                "Артикул": article,
                "Наименование артикула": entry["Наименование артикула"],
                "Поставщик": [supplier_id],
            }
            existing = target_by_article.get(article)
            if existing:
                row = existing[0]
                current_supplier_ids = sorted(
                    int(x.get("id"))
                    for x in (row.get("Поставщик") or [])
                    if isinstance(x, dict) and x.get("id") is not None
                )
                desired_supplier_ids = [int(supplier_id)]
                same = (
                    s(row.get("Название")) == body["Название"]
                    and s(row.get("Артикул")) == body["Артикул"]
                    and s(row.get("Наименование артикула")) == body["Наименование артикула"]
                    and current_supplier_ids == desired_supplier_ids
                )
                if same:
                    report["unchanged"] += 1
                else:
                    br.update_row(CATALOG_TABLE_ID, row["id"], body)
                    report["updated"] += 1
            else:
                created = br.create_row(CATALOG_TABLE_ID, body)
                target_by_article[article] = [created]
                report["created"] += 1

            if len(report["sample"]) < 20:
                report["sample"].append({
                    "Название": body["Название"],
                    "Артикул": body["Артикул"],
                    "Наименование артикула": body["Наименование артикула"],
                    "Поставщик": SUPPLIER_NAME,
                })

        report["complete"] = True

    except Exception as exc:
        report["errors"].append(str(exc))
        report["complete"] = False

    report["finished_at"] = now_iso()
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
