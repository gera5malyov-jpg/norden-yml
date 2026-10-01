#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "liga-kit"))

from liga_kit.feed import parse_feed
from liga_kit.http import SafeSession

BASEROW_URL = os.environ.get("BASEROW_URL", "http://147.78.67.6").rstrip("/")
TOKEN = os.environ.get("BASEROW_DATABASE_TOKEN", "").strip()
FEED_URL = "https://ligadivanov.ru/acrit.export/ym_vendormodel_2023.xml"
CATALOG_TABLE_ID = 156
SUPPLIERS_TABLE_ID = 157
SUPPLIER_NAME = "Лига диванов"
FIELD_PRICE_OLD = "Цена KIT до скидки"
FIELD_PRICE_SALE = "Цена KIT со скидкой"
FIELD_STOCK = "Остаток поставщика"
FIELD_STOCK_LEGACY = "Остаток Norden"
FIELD_PAYLOAD = "Ozon данные без изображений (JSON)"
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
        return r.json() if r.content else {}

    def fields(self, table_id):
        return self.request("GET", f"/api/database/fields/table/{table_id}/") or []

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

    def batch_create(self, table_id, items):
        for start in range(0, len(items), 100):
            self.request(
                "POST",
                f"/api/database/rows/table/{table_id}/batch/?user_field_names=true",
                data=json.dumps({"items": items[start:start+100]}, ensure_ascii=False),
            )

    def batch_update(self, table_id, items):
        for start in range(0, len(items), 100):
            self.request(
                "PATCH",
                f"/api/database/rows/table/{table_id}/batch/?user_field_names=true",
                data=json.dumps({"items": items[start:start+100]}, ensure_ascii=False),
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
    parts, seen = [], set()
    cur = s(category_id)
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


def feed_payload(offer):
    return {
        "source_id": offer.source_id,
        "vendor_code": offer.vendor_code,
        "vendor": offer.vendor,
        "description": offer.description,
        "currency": offer.currency,
        "barcode": offer.barcode,
        "weight": offer.weight,
        "dimensions": offer.dimensions,
        "source_url": offer.source_url,
        "country_of_origin": offer.country_of_origin,
        "manufacturer_warranty": bool(offer.manufacturer_warranty),
        "source_images": list(offer.images),
        "params": offer.params,
    }


def characteristic_values(offer):
    out = {}
    for title, values in (offer.params or {}).items():
        vals = [s(v) for v in values if s(v)]
        if title and vals:
            out[s(title)] = " / ".join(vals)
    if offer.weight:
        out["Вес, кг"] = offer.weight
    return out


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
        "characteristic_cells_written": 0,
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
    fields = {s(x.get("name")): x for x in br.fields(CATALOG_TABLE_ID)}
    stock_field = FIELD_STOCK if FIELD_STOCK in fields else FIELD_STOCK_LEGACY if FIELD_STOCK_LEGACY in fields else None
    required = {
        "Название", "Артикул", "Артикул KIT", "Категория", "Артикул поставщика",
        "Код для сайта", "Наличие", "Первое изображение URL", "Наименование артикула",
        "Поставщик", "Все изображения", FIELD_PRICE_OLD, FIELD_PRICE_SALE, FIELD_PAYLOAD,
    }
    missing = sorted(required - set(fields))
    if stock_field is None:
        missing.append(FIELD_STOCK)
    if missing:
        raise RuntimeError(f"Database is missing required existing fields: {missing}")

    suppliers = br.rows(SUPPLIERS_TABLE_ID)
    matches = [r for r in suppliers if norm(r.get("Поставщик")) == norm(SUPPLIER_NAME)]
    if not matches and not args.dry_run:
        matches = [br.create_row(SUPPLIERS_TABLE_ID, {"Поставщик": SUPPLIER_NAME})]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one supplier {SUPPLIER_NAME!r}, found {len(matches)}")
    supplier_id = int(matches[0]["id"])

    rows = br.rows(CATALOG_TABLE_ID)
    liga_rows = [r for r in rows if supplier_id in supplier_ids(r)]
    by_vendor = defaultdict(list)
    for row in liga_rows:
        key = norm(row.get("Артикул поставщика") or row.get("Наименование артикула"))
        if key:
            by_vendor[key].append(row)

    text_fields = {
        name for name, meta in fields.items()
        if s(meta.get("type")) in {"text", "long_text"}
    }

    creates, updates, seen = [], [], set()
    for offer in offers:
        key = norm(offer.vendor_code)
        seen.add(key)
        current = by_vendor.get(key, [])
        if len(current) > 1:
            report["errors"].append({"vendor_code": offer.vendor_code, "message": "duplicate Liga rows in Database"})
            continue

        path = category_path(offer.category_id, snapshot.categories)
        price = float(offer.price) if offer.price is not None else None
        old_price = round(price * 1.30, 2) if price is not None else None
        supplier_stock = int(offer.supplier_stock) if offer.supplier_stock is not None else (100 if offer.available else 0)
        article = "Liga-" + offer.vendor_code
        body = {
            "Название": offer.name,
            "Артикул": article,
            "Наименование артикула": offer.vendor_code,
            "Артикул поставщика": offer.vendor_code,
            "Код для сайта": article,
            "Поставщик": [supplier_id],
            "Наличие": bool(supplier_stock > 0),
            stock_field: supplier_stock,
            "Категория": path,
            FIELD_PRICE_OLD: old_price,
            FIELD_PRICE_SALE: price,
            FIELD_PAYLOAD: json.dumps(feed_payload(offer), ensure_ascii=False, separators=(",", ":")),
        }

        for title, value in characteristic_values(offer).items():
            if title in text_fields:
                body[title] = value
                report["characteristic_cells_written"] += 1

        if current:
            # Existing rows keep the public KIT image links and numeric KIT ID.
            updates.append({"id": current[0]["id"], **body})
            report["updated_rows"] += 1
        else:
            # Before the first KIT creation the public links temporarily hold supplier images.
            body["Первое изображение URL"] = offer.images[0] if offer.images else ""
            body["Все изображения"] = "\n".join(offer.images)
            creates.append(body)
            report["created_rows"] += 1

    if not args.max_items:
        for row in liga_rows:
            key = norm(row.get("Артикул поставщика") or row.get("Наименование артикула"))
            if key and key not in seen:
                vendor_code = s(row.get("Артикул поставщика") or row.get("Наименование артикула"))
                article = "Liga-" + vendor_code
                updates.append({
                    "id": row["id"],
                    "Артикул": article,
                    "Код для сайта": article,
                    "Наличие": False,
                    stock_field: 0,
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
