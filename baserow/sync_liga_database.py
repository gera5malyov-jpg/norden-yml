#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "liga-kit"))

from liga_kit.feed import parse_feed
from liga_kit.http import SafeSession

BASEROW_URL = os.environ.get("BASEROW_URL", "http://147.78.67.6").rstrip("/")
TOKEN = os.environ.get("BASEROW_DATABASE_TOKEN", "").strip()
FEED_URL = "https://ligadivanov.ru/acrit.export/ym_vendormodel_2023.xml"
CATALOG_TABLE_ID = 156
SUPPLIERS_TABLE_ID = 157
SUPPLIER_NAME = "Лига диванов"

FIELD_PRICE = "Цена Liga"
FIELD_STOCK = "Остаток Liga"
FIELD_CATEGORY = "Категория Liga"
FIELD_DESCRIPTION = "Описание Liga"
FIELD_BRAND = "Бренд Liga"
FIELD_SOURCE_IMAGES = "Изображения поставщика Liga"
FIELD_SOURCE_URL = "Ссылка поставщика"

REPORT = ROOT / "baserow" / "last_liga_database_sync.json"


def s(v):
    return str(v or "").strip()


def norm(v):
    return unicodedata.normalize("NFKC", s(v)).casefold()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class Baserow:
    def __init__(self):
        if not TOKEN:
            raise RuntimeError("BASEROW_DATABASE_TOKEN is missing")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token {TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def request(self, method, path, **kwargs):
        r = self.session.request(method, BASEROW_URL + path, timeout=90, **kwargs)
        if not r.ok:
            raise RuntimeError(f"Baserow {method} {path} -> HTTP {r.status_code}: {r.text[:1200]}")
        if not r.content:
            return {}
        return r.json()

    def fields(self, table_id):
        return self.request("GET", f"/api/database/fields/table/{table_id}/") or []

    def ensure_field(self, table_id, name, field_type, **extra):
        fields = self.fields(table_id)
        matches = [x for x in fields if s(x.get("name")) == name]
        if matches:
            return matches[0]
        body = {"name": name, "type": field_type, **extra}
        return self.request("POST", f"/api/database/fields/table/{table_id}/", data=json.dumps(body, ensure_ascii=False))

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

    def batch_create(self, table_id, items):
        for start in range(0, len(items), 100):
            batch = items[start:start+100]
            self.request(
                "POST",
                f"/api/database/rows/table/{table_id}/batch/?user_field_names=true",
                data=json.dumps({"items": batch}, ensure_ascii=False),
            )

    def batch_update(self, table_id, items):
        for start in range(0, len(items), 100):
            batch = items[start:start+100]
            self.request(
                "PATCH",
                f"/api/database/rows/table/{table_id}/batch/?user_field_names=true",
                data=json.dumps({"items": batch}, ensure_ascii=False),
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


def category_path(category_id, categories):
    if not category_id:
        return ""
    parts, seen = [], set()
    cur = str(category_id)
    while cur and cur not in seen:
        seen.add(cur)
        row = categories.get(cur)
        if row is None:
            break
        if s(row.name):
            parts.append(s(row.name))
        cur = s(row.parent_id)
    parts.reverse()
    return " > ".join(parts)


def offer_characteristics(offer):
    result = {}
    for title, values in (offer.params or {}).items():
        vals = [s(v) for v in values if s(v)]
        if title and vals:
            result[s(title)] = " / ".join(vals)
    if offer.barcode:
        result["Штрихкод"] = offer.barcode
    if offer.country_of_origin:
        result["Страна производства"] = offer.country_of_origin
    if offer.manufacturer_warranty:
        result["Гарантия производителя"] = "Да"
    if offer.weight:
        result["Вес"] = offer.weight
    if offer.dimensions:
        result["Габариты"] = offer.dimensions
    result["Артикул Liga"] = offer.vendor_code
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-items", type=int, default=0)
    args = ap.parse_args()

    report = {
        "started_at": now_iso(),
        "source": FEED_URL,
        "supplier": SUPPLIER_NAME,
        "offers": 0,
        "created_rows": 0,
        "updated_rows": 0,
        "zeroed_absent": 0,
        "fields_created": [],
        "errors": [],
        "complete": False,
    }

    http = SafeSession()
    with tempfile.TemporaryDirectory(prefix="liga-db-") as td:
        path = str(Path(td) / "feed.xml")
        http.download_to_file(FEED_URL, path)
        snapshot = parse_feed(path)

    offers = snapshot.offers[:args.max_items] if args.max_items else snapshot.offers
    report["offers"] = len(offers)

    br = Baserow()

    supplier_rows = br.rows(SUPPLIERS_TABLE_ID)
    matches = [r for r in supplier_rows if norm(r.get("Поставщик")) == norm(SUPPLIER_NAME)]
    if not matches and not args.dry_run:
        created = br.create_row(SUPPLIERS_TABLE_ID, {"Поставщик": SUPPLIER_NAME})
        matches = [created]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one supplier {SUPPLIER_NAME!r}, found {len(matches)}")
    supplier_id = int(matches[0]["id"])

    core_specs = [
        (FIELD_PRICE, "number", {"number_decimal_places": 2, "number_negative": False}),
        (FIELD_STOCK, "number", {"number_decimal_places": 0, "number_negative": False}),
        (FIELD_CATEGORY, "text", {}),
        (FIELD_DESCRIPTION, "long_text", {}),
        (FIELD_BRAND, "text", {}),
        (FIELD_SOURCE_IMAGES, "long_text", {}),
        (FIELD_SOURCE_URL, "url", {}),
        ("Штрихкод", "text", {}),
        ("Страна производства", "text", {}),
        ("Гарантия производителя", "text", {}),
        ("Вес", "text", {}),
        ("Габариты", "text", {}),
        ("Артикул Liga", "text", {}),
    ]

    existing_fields = {s(x.get("name")): x for x in br.fields(CATALOG_TABLE_ID)}
    if not args.dry_run:
        for name, typ, extra in core_specs:
            if name not in existing_fields:
                created = br.ensure_field(CATALOG_TABLE_ID, name, typ, **extra)
                existing_fields[name] = created
                report["fields_created"].append(name)

        char_names = sorted({
            s(title)
            for offer in offers
            for title in offer_characteristics(offer)
            if s(title)
        })
        for name in char_names:
            if name not in existing_fields:
                created = br.ensure_field(CATALOG_TABLE_ID, name, "text")
                existing_fields[name] = created
                report["fields_created"].append(name)

    rows = br.rows(CATALOG_TABLE_ID)
    liga_rows = [r for r in rows if supplier_id in supplier_ids(r)]
    by_vendor = defaultdict(list)
    for row in liga_rows:
        key = norm(row.get("Артикул поставщика") or row.get("Наименование артикула"))
        if key:
            by_vendor[key].append(row)

    creates = []
    updates = []
    seen = set()

    for offer in offers:
        key = norm(offer.vendor_code)
        seen.add(key)
        current = by_vendor.get(key, [])
        if len(current) > 1:
            report["errors"].append({"vendor_code": offer.vendor_code, "message": "duplicate Liga rows in Database"})
            continue

        stock = 100 if offer.available else 0
        path = category_path(offer.category_id, snapshot.categories)
        body = {
            "Название": offer.name,
            "Артикул": offer.kit_sku,
            "Наименование артикула": offer.vendor_code,
            "Артикул поставщика": offer.vendor_code,
            "Код для сайта": offer.kit_sku,
            "Поставщик": [supplier_id],
            "Наличие": bool(offer.available),
            FIELD_PRICE: float(offer.price) if offer.price is not None else None,
            FIELD_STOCK: stock,
            FIELD_CATEGORY: path,
            "Категория": path,
            FIELD_DESCRIPTION: offer.description,
            FIELD_BRAND: offer.vendor,
            FIELD_SOURCE_URL: offer.source_url,
            FIELD_SOURCE_IMAGES: "\n".join(offer.images),
        }
        body.update(offer_characteristics(offer))

        # Public image fields become KIT URLs after the final stage.
        # For a new Database row they may temporarily contain supplier URLs so
        # the Database -> KIT stage can create the first KIT card.
        if not current:
            body["Первое изображение URL"] = offer.images[0] if offer.images else ""
            body["Все изображения"] = "\n".join(offer.images)
            creates.append(body)
            report["created_rows"] += 1
        else:
            row = current[0]
            # Do not overwrite KIT image URLs or numeric KIT code on existing rows.
            updates.append({"id": row["id"], **body})
            report["updated_rows"] += 1

    if not args.max_items:
        for row in liga_rows:
            key = norm(row.get("Артикул поставщика") or row.get("Наименование артикула"))
            if key and key not in seen:
                updates.append({
                    "id": row["id"],
                    FIELD_STOCK: 0,
                    "Наличие": False,
                })
                report["zeroed_absent"] += 1

    if not args.dry_run:
        if creates:
            br.batch_create(CATALOG_TABLE_ID, creates)
        if updates:
            br.batch_update(CATALOG_TABLE_ID, updates)

    report["finished_at"] = now_iso()
    report["complete"] = not report["errors"]
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
