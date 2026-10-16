#!/usr/bin/env python3
"""Aletan supplier public CSV -> stable YML Pro feed. No KIT/Webasyst API."""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

import requests

SOURCE = "https://aletan.ru/upload/aletan_catalog_export.csv"
DESTINATION = Path("aletan-yml/aletan.yml")
MIN_ROWS = int(os.getenv("ALETAN_MIN_ROWS", "750"))
BRAND = "Алетан"


def clean(value):
    return " ".join(str(value or "").replace("\u00a0", " ").split())


def xml_safe(value):
    # XML 1.0 cannot contain certain control characters.
    return "".join(ch for ch in str(value or "")
                   if ch in "\t\n\r" or ord(ch) >= 0x20)


def decode(data):
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise RuntimeError("Unknown supplier CSV encoding")


def valid_url(value, site_only=False):
    try:
        u = urlsplit(str(value or "").strip())
        if u.scheme not in ("https", "http") or not u.hostname:
            return False
        if site_only and u.hostname not in ("aletan.ru", "www.aletan.ru"):
            return False
        return True
    except ValueError:
        return False


def price(value):
    raw = clean(value).replace(" ", "").replace(",", ".")
    if not raw:
        return ""
    try:
        number = Decimal(raw)
    except InvalidOperation:
        return ""
    if not number.is_finite() or number <= 0:
        return ""
    return format(number, "f").rstrip("0").rstrip(".") if "." in format(number, "f") else format(number, "f")


def safe_stock(value):
    raw = clean(value).replace(" ", "")
    try:
        number = int(raw)
    except ValueError:
        return 0
    return min(100000, max(0, number))


def article(row):
    return clean(row.get("vendorCode") or row.get("id"))


def load_rows(data):
    reader = csv.DictReader(io.StringIO(decode(data)), delimiter=";")
    mandatory = {"name", "vendorCode", "url", "price", "picture", "category", "available", "stock", "vendor"}
    if not reader.fieldnames or not mandatory.issubset(set(reader.fieldnames)):
        raise RuntimeError(f"Unexpected Aletan CSV columns: {reader.fieldnames}")
    rows = []
    skipped = []
    for index, item in enumerate(reader, start=2):
        if clean(item.get("vendor")).casefold().replace("ё", "е") != "алетан":
            continue
        code = article(item)
        url = clean(item.get("url"))
        amount = price(item.get("price"))
        pictures = [clean(x) for x in (item.get("picture") or "").split(";") if valid_url(x)]
        if not code or not clean(item.get("name")) or not valid_url(url, site_only=True) or not amount or not pictures:
            skipped.append((index, code, url))
            continue
        item["_code"] = code
        item["_url"] = url
        item["_price"] = amount
        item["_pics"] = list(dict.fromkeys(pictures))
        rows.append(item)
    if skipped:
        raise RuntimeError(f"Invalid supplier records: {len(skipped)}. Examples: {skipped[:10]}")
    if len(rows) < MIN_ROWS:
        raise RuntimeError(f"Suspiciously small catalog: {len(rows)} rows (min {MIN_ROWS})")
    return rows


def unique_codes(rows):
    counts = Counter(row["_code"] for row in rows)
    ids = set()
    for row in rows:
        code = row["_code"]
        # Vendor's product code is NOT unique for color/fabric variants.
        # Use deterministic URL suffix on every duplicated code to prevent
        # accidental YML Pro SKU collisions, preserve original as param.
        if counts[code] > 1:
            suffix = hashlib.sha256(row["_url"].encode("utf-8")).hexdigest()[:12]
            export_code = f"{code}-{suffix}"
        else:
            export_code = code
        if export_code in ids:
            raise RuntimeError(f"Duplicate export identifier: {export_code}")
        ids.add(export_code)
        row["_export_code"] = export_code
    return rows


FIELDS = [
    ("collection", "Коллекция"),
    ("material", "Материал"),
    ("color", "Цвет"),
    ("dimensions", "Габариты"),
    ("width", "Ширина"),
    ("height", "Высота"),
    ("depth", "Глубина"),
    ("weight", "Вес"),
    ("country_of_origin", "Страна производства"),
    ("cartons", "Количество упаковок"),
    ("cartons_dimensions", "Габариты упаковок"),
    ("assembly", "Сборка"),
    ("production_time", "Срок изготовления"),
    ("assembly_instruction_url", "Инструкция по сборке"),
    ("video", "Видео"),
    ("Model3D", "3D-модель"),
]


def build(rows):
    rows = unique_codes(rows)
    root = ET.Element("yml_catalog", date=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    shop = ET.SubElement(root, "shop")
    for field, value in (("name", "Алетан"), ("company", "Алетан"), ("url", "https://aletan.ru")):
        ET.SubElement(shop, field).text = value
    ET.SubElement(ET.SubElement(shop, "currencies"), "currency", {"id": "RUB", "rate": "1"})
    categories = ET.SubElement(shop, "categories")
    ET.SubElement(categories, "category", {"id": "1"}).text = "Алетан"
    cat_names = sorted({clean(row.get("category")) or "Без категории" for row in rows})
    # Stable IDs even when the supplier adds a new category in the CSV.
    cat_to_id = {
        cat: str(int(hashlib.sha256(cat.encode("utf-8")).hexdigest()[:12], 16) % 1_999_000_000 + 1000)
        for cat in cat_names
    }
    if len(set(cat_to_id.values())) != len(cat_names):
        raise RuntimeError("Hashed category ID collision; refusing to publish feed")
    for name, cat_id in cat_to_id.items():
        ET.SubElement(categories, "category", {"id": cat_id, "parentId": "1"}).text = xml_safe(name)
    offers = ET.SubElement(shop, "offers")
    for row in sorted(rows, key=lambda item: item["_export_code"]):
        is_available = clean(row.get("available")).lower() in ("true", "1", "yes", "да")
        stock = safe_stock(row.get("stock")) if is_available else 0
        offer = ET.SubElement(offers, "offer",
                              {"id": row["_export_code"], "available": "true" if is_available else "false"})
        fields = (
            ("categoryId", cat_to_id[clean(row.get("category")) or "Без категории"]),
            ("url", row["_url"]),
            ("name", clean(row.get("name"))),
            ("vendor", BRAND),
            ("vendorCode", row["_export_code"]),
            ("model", row["_code"]),
            ("price", row["_price"]),
            ("currencyId", "RUB"),
            ("count", str(stock)),
        )
        for tag, value in fields:
            ET.SubElement(offer, tag).text = xml_safe(value)
        old_price = price(row.get("oldprice"))
        if old_price and Decimal(old_price) > Decimal(row["_price"]):
            ET.SubElement(offer, "oldprice").text = old_price
        for picture in row["_pics"]:
            ET.SubElement(offer, "picture").text = xml_safe(picture)
        description = row.get("description")
        if clean(description):
            ET.SubElement(offer, "description").text = xml_safe(description)
        ET.SubElement(offer, "param", {"name": "Оригинальный артикул производителя"}).text = xml_safe(row["_code"])
        for key, name in FIELDS:
            value = clean(row.get(key))
            if value:
                ET.SubElement(offer, "param", {"name": name}).text = xml_safe(value)
    ET.indent(root, space="  ")
    payload = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8") + b"\n"
    ET.fromstring(payload)
    return payload


def main():
    response = requests.get(SOURCE, timeout=(20, 120),
                            headers={"User-Agent": "Mozilla/5.0 (compatible; AletanYMLProFeed/1.0)"})
    response.raise_for_status()
    if len(response.content) < 100_000:
        raise RuntimeError("Source CSV response unexpectedly short")
    rows = load_rows(response.content)
    payload = build(rows)
    previous_ids = set()
    if DESTINATION.exists():
        previous_ids = {el.attrib["id"] for el in ET.parse(DESTINATION).findall("./shop/offers/offer")}
    current_ids = {el.attrib["id"] for el in ET.fromstring(payload).findall("./shop/offers/offer")}
    if previous_ids and len(previous_ids - current_ids) > len(previous_ids) * 0.20:
        raise RuntimeError(f"Unsafe catalog loss: {len(previous_ids-current_ids)} of {len(previous_ids)} previous SKUs missing")
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    temporary = DESTINATION.with_suffix(".yml.tmp")
    temporary.write_bytes(payload)
    temporary.replace(DESTINATION)
    dups = sum(n - 1 for n in Counter(row["_code"] for row in rows).values() if n > 1)
    print(f"Published {len(rows)} Aletan products; unique YML IDs: {len(current_ids)}; duplicate vendor codes resolved: {dups}; bytes: {len(payload)}", flush=True)
    print(f"Source: {SOURCE}\nFeed: {DESTINATION}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ALETAN FEED FAILURE: {exc}", file=sys.stderr)
        raise
