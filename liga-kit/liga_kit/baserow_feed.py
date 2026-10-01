from __future__ import annotations

import json
import os
import unicodedata
from decimal import Decimal, InvalidOperation

import requests

from .model import FeedSnapshot, LigaCategory, LigaOffer
from .rules import to_kit_sku

BASEROW_URL = os.environ.get("BASEROW_URL", "http://147.78.67.6").rstrip("/")
TOKEN = os.environ.get("BASEROW_DATABASE_TOKEN", "").strip()
CATALOG_TABLE_ID = 156
SUPPLIERS_TABLE_ID = 157
SUPPLIER_NAME = "Лига диванов"

FIELD_PRICE = "Цена KIT со скидкой"
FIELD_OLD_PRICE = "Цена KIT до скидки"
FIELD_PAYLOAD = "Ozon данные без изображений (JSON)"


def s(v):
    return str(v or "").strip()


def norm(v):
    return unicodedata.normalize("NFKC", s(v)).casefold()


def dec(v):
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None


class Baserow:
    def __init__(self):
        if not TOKEN:
            raise RuntimeError("BASEROW_DATABASE_TOKEN is missing")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Token {TOKEN}", "Accept": "application/json"})

    def rows(self, table_id):
        out, page = [], 1
        while True:
            r = self.session.get(
                BASEROW_URL + f"/api/database/rows/table/{table_id}/?user_field_names=true&size=200&page={page}",
                timeout=90,
            )
            if not r.ok:
                raise RuntimeError(f"Baserow GET rows -> HTTP {r.status_code}: {r.text[:1200]}")
            data = r.json()
            out.extend(x for x in data.get("results", []) if isinstance(x, dict))
            if not data.get("next"):
                break
            page += 1
        return out


def supplier_ids(row):
    out = set()
    for x in row.get("Поставщик") or []:
        if isinstance(x, dict) and x.get("id") is not None:
            try:
                out.add(int(x["id"]))
            except Exception:
                pass
    return out


def build_categories(paths):
    categories, path_to_id, seq = {}, {}, 1
    for raw in paths:
        parts = [x.strip() for x in s(raw).split(">") if x.strip()]
        parent, chain = None, []
        for title in parts:
            chain.append(title)
            key = " > ".join(chain)
            if key not in path_to_id:
                cid = f"db-{seq}"
                seq += 1
                path_to_id[key] = cid
                categories[cid] = LigaCategory(cid, title, parent)
            parent = path_to_id[key]
    return categories, path_to_id


def load_payload(row):
    try:
        value = json.loads(s(row.get(FIELD_PAYLOAD)) or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def load_snapshot():
    br = Baserow()
    suppliers = br.rows(SUPPLIERS_TABLE_ID)
    matches = [r for r in suppliers if norm(r.get("Поставщик")) == norm(SUPPLIER_NAME)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one supplier {SUPPLIER_NAME!r}, found {len(matches)}")
    supplier_id = int(matches[0]["id"])

    rows = [r for r in br.rows(CATALOG_TABLE_ID) if supplier_id in supplier_ids(r)]
    paths = [s(r.get("Категория")) for r in rows]
    categories, path_to_id = build_categories(paths)

    offers = []
    for row in rows:
        vendor_code = s(row.get("Артикул поставщика") or row.get("Наименование артикула"))
        if not vendor_code:
            continue
        # Database article is capitalized as Liga-..., but KIT keeps its established liga-... SKU.
        sku = to_kit_sku(vendor_code)

        payload = load_payload(row)
        category_path = s(row.get("Категория"))
        category_id = path_to_id.get(category_path) if category_path else None

        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        params = {
            s(k): [s(x) for x in (v if isinstance(v, list) else [v]) if s(x)]
            for k, v in params.items()
            if s(k)
        }
        images = [s(x) for x in (payload.get("source_images") or []) if s(x)]

        offers.append(LigaOffer(
            source_id=s(payload.get("source_id") or row.get("id")),
            vendor_code=vendor_code,
            kit_sku=sku,
            available=bool(row.get("Наличие")),
            category_id=category_id,
            name=s(row.get("Название")) or vendor_code,
            description=s(payload.get("description")),
            vendor=s(payload.get("vendor")),
            price=dec(row.get(FIELD_PRICE)),
            currency=s(payload.get("currency")) or "RUB",
            barcode=s(payload.get("barcode")),
            weight=s(payload.get("weight")),
            dimensions=s(payload.get("dimensions")),
            source_url=s(payload.get("source_url")),
            country_of_origin=s(payload.get("country_of_origin")),
            manufacturer_warranty=bool(payload.get("manufacturer_warranty")),
            images=images,
            params=params,
            old_price=dec(row.get(FIELD_OLD_PRICE)),
        ))

    return FeedSnapshot(categories=categories, offers=offers, complete=True)
