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

FIELD_PRICE = "Цена Liga"
FIELD_STOCK = "Остаток Liga"
FIELD_CATEGORY = "Категория Liga"
FIELD_DESCRIPTION = "Описание Liga"
FIELD_BRAND = "Бренд Liga"
FIELD_SOURCE_IMAGES = "Изображения поставщика Liga"
FIELD_SOURCE_URL = "Ссылка поставщика"

SYSTEM_FIELDS = {
    "id", "order", "Название", "Артикул", "Артикул KIT", "Яндекс SKU", "WB nmID",
    "Категория", "Артикул поставщика", "Код для сайта", "Цена Webasyst", "Цена Ozon",
    "Наличие", "Первое изображение URL", "Тип товара Webasyst", "Дата создания в KIT",
    "Дата изменения", "Последняя синхронизация", "Наименование артикула", "Поставщик",
    "Все изображения", "Первое изображение", FIELD_PRICE, FIELD_STOCK, FIELD_CATEGORY,
    FIELD_DESCRIPTION, FIELD_BRAND, FIELD_SOURCE_IMAGES, FIELD_SOURCE_URL,
    "Штрихкод", "Страна производства", "Гарантия производителя", "Вес", "Габариты",
    "Артикул Liga",
}


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
        self.session.headers.update({
            "Authorization": f"Token {TOKEN}",
            "Accept": "application/json",
        })

    def request(self, path):
        r = self.session.get(BASEROW_URL + path, timeout=90)
        if not r.ok:
            raise RuntimeError(f"Baserow GET {path} -> HTTP {r.status_code}: {r.text[:1200]}")
        return r.json()

    def rows(self, table_id):
        out = []
        page = 1
        while True:
            data = self.request(
                f"/api/database/rows/table/{table_id}/?user_field_names=true&size=200&page={page}"
            )
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


def split_images(value):
    out = []
    for line in s(value).replace(",", "\n").splitlines():
        url = line.strip()
        if url and url.startswith(("http://", "https://")) and url not in out:
            out.append(url)
    return out


def parse_bool(value):
    return s(value).casefold() in {"1", "true", "yes", "y", "да"}


def build_categories(paths):
    categories = {}
    path_to_id = {}
    seq = 1
    for raw in paths:
        parts = [x.strip() for x in s(raw).split(">") if x.strip()]
        parent = None
        chain = []
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


def load_snapshot():
    br = Baserow()
    suppliers = br.rows(SUPPLIERS_TABLE_ID)
    matches = [r for r in suppliers if norm(r.get("Поставщик")) == norm(SUPPLIER_NAME)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one supplier {SUPPLIER_NAME!r}, found {len(matches)}")
    supplier_id = int(matches[0]["id"])

    rows = [r for r in br.rows(CATALOG_TABLE_ID) if supplier_id in supplier_ids(r)]
    paths = [s(r.get(FIELD_CATEGORY) or r.get("Категория")) for r in rows]
    categories, path_to_id = build_categories(paths)

    offers = []
    for row in rows:
        vendor_code = s(row.get("Артикул поставщика") or row.get("Наименование артикула"))
        if not vendor_code:
            continue
        sku = s(row.get("Артикул")) or to_kit_sku(vendor_code)
        if not sku.startswith("liga-"):
            sku = to_kit_sku(vendor_code)

        stock = dec(row.get(FIELD_STOCK))
        available = bool(stock is not None and stock > 0)
        category_path = s(row.get(FIELD_CATEGORY) or row.get("Категория"))
        category_id = path_to_id.get(category_path) if category_path else None

        params = {}
        for key, value in row.items():
            if key in SYSTEM_FIELDS or key.startswith("Ozon") or "Norden" in key:
                continue
            if isinstance(value, (dict, list, bool, int, float)) or value in (None, ""):
                continue
            text = s(value)
            if text:
                params[key] = [text]

        offers.append(LigaOffer(
            source_id=s(row.get("id")),
            vendor_code=vendor_code,
            kit_sku=sku,
            available=available,
            category_id=category_id,
            name=s(row.get("Название")) or vendor_code,
            description=s(row.get(FIELD_DESCRIPTION)),
            vendor=s(row.get(FIELD_BRAND)),
            price=dec(row.get(FIELD_PRICE)),
            currency="RUB",
            barcode=s(row.get("Штрихкод")),
            weight=s(row.get("Вес")),
            dimensions=s(row.get("Габариты")),
            source_url=s(row.get(FIELD_SOURCE_URL)),
            country_of_origin=s(row.get("Страна производства")),
            manufacturer_warranty=parse_bool(row.get("Гарантия производителя")),
            images=split_images(row.get(FIELD_SOURCE_IMAGES) or row.get("Все изображения")),
            params=params,
        ))

    return FeedSnapshot(categories=categories, offers=offers, complete=True)
