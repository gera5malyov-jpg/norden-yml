from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import hashlib
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from samson_kit.http import SafeSession
from samson_kit.kit_client import KitClient
from samson_kit.rules import calculate_prices

PERSONAL_URL = "https://komus-opt.ru/personal/"
TARGET_WAREHOUSE = "СПБ"
SKU_PREFIX = "kom-"
MAX_IMAGES = max(1, int(os.environ.get("KOMUS_MAX_IMAGES", "1") or "1"))


def _tag(elem):
    return elem.tag.split("}")[-1]


def _text(node, tag, default=""):
    for child in list(node):
        if _tag(child) == tag:
            return str(child.text or "").strip()
    return default


def _money(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _kit_money(value):
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_CEILING)
    except Exception:
        return None


def _norm(value):
    return str(value or "").strip().casefold()


def _clean_html(value):
    text = str(value or "")
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _safe_int(value, default=0):
    try:
        return max(0, int(Decimal(str(value).replace(",", "."))))
    except Exception:
        return default


def _current_stock(variant, warehouse_id):
    for row in variant.get("stocks") or []:
        if str(row.get("warehouse_id") or "") == str(warehouse_id):
            try:
                return int(row.get("quantity") or 0)
            except Exception:
                return None
    return 0


def _index_komus_variants(kit):
    buckets = defaultdict(list)
    seen_ids = set()
    for row in kit.iter_variants({"name": SKU_PREFIX}):
        if not isinstance(row, dict):
            continue
        sku = str(row.get("sku") or "").strip()
        if not sku.casefold().startswith(SKU_PREFIX):
            continue
        vid = str(row.get("id") or "").strip()
        if vid:
            key = (sku.casefold(), vid)
            if key in seen_ids:
                continue
            seen_ids.add(key)
        buckets[sku.casefold()].append(row)
    unique = {k: v[0] for k, v in buckets.items() if len(v) == 1}
    dup = {k: v for k, v in buckets.items() if len(v) > 1}
    return unique, dup


class KomusPersonalSource:
    def __init__(self, login, password):
        self.login = str(login or "").strip()
        self.password = str(password or "").strip()
        if not self.login or not self.password:
            raise RuntimeError("KOMUS_LOGIN/KOMUS_PASSWORD are not configured")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        })

    def _login(self):
        r = self.session.get(PERSONAL_URL, timeout=90)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        form = next(
            (
                f for f in soup.find_all("form")
                if any((x.get("type") or "").lower() == "password" for x in f.find_all("input"))
            ),
            None,
        )
        if form is None:
            raise RuntimeError("Komus login form not found")
        data = {}
        for inp in form.find_all("input"):
            name = (inp.get("name") or "").strip()
            if not name:
                continue
            typ = (inp.get("type") or "text").lower()
            if typ in ("hidden", "submit", "checkbox"):
                data[name] = inp.get("value") or ""
        data["USER_LOGIN"] = self.login
        data["USER_PASSWORD"] = self.password
        action = urllib.parse.urljoin(r.url, form.get("action") or r.url)
        response = self.session.post(action, data=data, timeout=90, allow_redirects=True)
        response.raise_for_status()
        page = self.session.get(PERSONAL_URL, timeout=90)
        page.raise_for_status()
        low = page.text.casefold()
        if "полный ассортимент со специальными ценами" not in low:
            raise RuntimeError("Komus authentication failed")
        if "полный товарный каталог в xml" not in low:
            raise RuntimeError("Komus XML download link not available after login")
        return page

    def _download_links(self, page):
        soup = BeautifulSoup(page.text, "html.parser")
        wanted = {
            "price": "полный ассортимент со специальными ценами",
            "xml": "полный товарный каталог в xml",
        }
        links = {}
        for a in soup.find_all("a", href=True):
            label = " ".join(a.stripped_strings).casefold()
            for key, phrase in wanted.items():
                if phrase in label:
                    links[key] = urllib.parse.urljoin(page.url, a["href"])
        if set(links) != {"price", "xml"}:
            raise RuntimeError("Required Komus personal download links not found")
        return links

    def download(self, folder):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        page = self._login()
        links = self._download_links(page)
        result = {}
        for key, url in links.items():
            response = self.session.get(url, timeout=240, allow_redirects=True)
            response.raise_for_status()
            zip_path = folder / f"{key}.zip"
            zip_path.write_bytes(response.content)
            if not zipfile.is_zipfile(zip_path):
                raise RuntimeError(f"Komus {key} response is not a ZIP archive")
            with zipfile.ZipFile(zip_path) as archive:
                members = [m for m in archive.namelist() if not m.endswith("/")]
                if key == "xml":
                    member = next((m for m in members if m.lower().endswith((".xml", ".yml"))), None)
                else:
                    member = next((m for m in members if m.lower().endswith(".csv")), None)
                if not member:
                    raise RuntimeError(f"Expected file missing in Komus {key} archive")
                output = folder / os.path.basename(member)
                with archive.open(member) as src, open(output, "wb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
                result[key] = output
        return result["xml"], result["price"]


def load_special_prices(csv_path):
    raw = Path(csv_path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1251", "replace")
    lines = text.splitlines()
    header_index = None
    for i, line in enumerate(lines[:20]):
        if "Артикул" in line and "Спец.ЦЕНА" in line:
            header_index = i
            break
    if header_index is None:
        raise RuntimeError("Komus price CSV header not found")

    rows = {}
    reader = csv.DictReader(lines[header_index:], delimiter=";")
    for row in reader:
        art = str(row.get("Артикул") or "").strip()
        if not art:
            continue
        rows[art] = row
    return rows


def load_categories(xml_path):
    categories = {}
    inside = False
    for event, elem in ET.iterparse(xml_path, events=("start", "end")):
        tag = _tag(elem)
        if event == "start" and tag == "categories":
            inside = True
            continue
        if event == "end" and tag == "categories":
            elem.clear()
            break
        if event == "end" and inside and tag == "category":
            source_id = str(elem.attrib.get("id") or "").strip()
            title = str(elem.text or "").strip()
            parent = str(elem.attrib.get("parentId") or "").strip()
            if source_id and title:
                categories[source_id] = {
                    "id": source_id,
                    "title": title,
                    "parent_id": parent or None,
                }
            elem.clear()
    return categories


def iter_offers(xml_path):
    for event, elem in ET.iterparse(xml_path, events=("end",)):
        if _tag(elem) != "offer":
            continue
        try:
            art = str(elem.attrib.get("id") or "").strip()
            if not art:
                continue
            params = defaultdict(list)
            pictures = []
            barcodes = []
            for child in list(elem):
                tag = _tag(child)
                value = str(child.text or "").strip()
                if tag == "param":
                    title = str(child.attrib.get("name") or "").strip()
                    if title and value and value not in params[title]:
                        params[title].append(value)
                elif tag == "picture" and value and value not in pictures:
                    pictures.append(value)
                elif tag == "barcode" and value and value not in barcodes:
                    barcodes.append(value)
            yield {
                "art": art,
                "sku": SKU_PREFIX + art,
                "name": _text(elem, "name", art) or art,
                "vendor_code": _text(elem, "vendorCode"),
                "category_id": _text(elem, "categoryId"),
                "base_price": _text(elem, "price"),
                "quantity": _safe_int(_text(elem, "quantity", "0")),
                "brand": _text(elem, "vendor"),
                "model": _text(elem, "model"),
                "description": _clean_html(_text(elem, "description")),
                "country": _text(elem, "country_of_origin"),
                "tnved": _text(elem, "tnved"),
                "vat": _text(elem, "VAT"),
                "type_prefix": _text(elem, "typePrefix"),
                "source_url": _text(elem, "url"),
                "images": pictures[:MAX_IMAGES],
                "barcodes": barcodes,
                "params": dict(params),
            }
        finally:
            elem.clear()



def _csv_category_id(parts):
    key=" > ".join(str(x or "").strip() for x in parts if str(x or "").strip())
    return "komus-csv-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


def _add_csv_category_path(categories, row):
    direction=str(row.get("Товарное направление") or "").strip()
    category=str(row.get("Товарная категория") or "").strip()
    group=str(row.get("Товарная группа") or "").strip()
    titles=[x for x in (direction,category,group) if x]
    if not titles:
        titles=["Комус"]
    parent=None
    parts=[]
    last=None
    for title in titles:
        parts.append(title)
        cid=_csv_category_id(parts)
        categories.setdefault(cid,{
            "id":cid,
            "title":title,
            "parent_id":parent,
        })
        parent=cid
        last=cid
    return last


def _price_only_offer(art, row, categories):
    category_id=_add_csv_category_path(categories,row)
    image=str(row.get("Изображние товара") or "").strip()
    barcodes=[
        x.strip() for x in str(row.get("Штрих-код") or "").split(",")
        if x.strip()
    ]
    params={}
    for title in ("Товарное направление","Товарная категория","Товарная группа"):
        value=str(row.get(title) or "").strip()
        if value:
            params[title]=[value]
    return {
        "art":art,
        "sku":SKU_PREFIX+art,
        "name":str(row.get("Наименование товара") or art).strip() or art,
        "vendor_code":str(row.get("Код производителя") or "").strip(),
        "category_id":category_id,
        "base_price":str(row.get("Базовая цена, руб.") or "").strip(),
        "quantity":_safe_int(row.get("Наличие на складе") or "0"),
        "brand":str(row.get("Торговая марка") or "").strip(),
        "model":str(row.get("Код производителя") or "").strip(),
        "description":_clean_html(row.get("Доп. информация") or ""),
        "country":str(row.get("Страна-производитель") or "").strip(),
        "tnved":str(row.get("Код ТНВЭД") or "").strip(),
        "vat":str(row.get("НДС") or "").strip(),
        "type_prefix":str(row.get("Товарная группа") or "").strip(),
        "source_url":"",
        "images":[image] if image else [],
        "barcodes":barcodes,
        "params":params,
    }


def iter_all_offers(xml_path, price_rows, categories):
    xml_arts=set()
    for offer in iter_offers(xml_path):
        xml_arts.add(offer["art"])
        yield offer
    for art,row in price_rows.items():
        if art in xml_arts:
            continue
        yield _price_only_offer(art,row,categories)


class KomusSyncRunner:
    def __init__(
        self,
        xml_path,
        price_csv_path,
        kit,
        http,
        *,
        dry_run=False,
        skip_items=0,
        max_items=None,
        max_new=None,
        new_only=False,
        only_art=None,
    ):
        self.xml_path = str(xml_path)
        self.price_csv_path = str(price_csv_path)
        self.kit = kit
        self.http = http
        self.dry_run = bool(dry_run)
        self.skip_items = max(0, int(skip_items or 0))
        self.max_items = int(max_items) if max_items not in (None, "", 0, "0") else None
        self.max_new = int(max_new) if max_new not in (None, "", 0, "0") else None
        self.new_only = bool(new_only)
        self.only_art = str(only_art or "").strip() or None

        self.kit_categories = []
        self.kit_characteristics = []
        self.category_cache = {}
        self.category_index = {}
        self.characteristic_index = {}
        self.report = {
            "status": "running",
            "dry_run": self.dry_run,
            "catalog_complete": False,
            "source_rows_seen": 0,
            "eligible_in_stock": 0,
            "price_rows": 0,
            "special_price_used": 0,
            "base_price_fallback": 0,
            "new_products_created": 0,
            "new_limit_skipped": 0,
            "existing_variants_seen": 0,
            "price_changes": 0,
            "stock_changes": 0,
            "absent_to_zero": 0,
            "duplicate_kit_skus": 0,
            "warning_count": 0,
            "warnings": [],
            "error_count": 0,
            "errors": [],
            "skip_items": self.skip_items,
            "next_offset": self.skip_items,
            "max_items": self.max_items,
            "max_new": self.max_new,
            "new_only": self.new_only,
            "only_art": self.only_art,
            "warehouse": TARGET_WAREHOUSE,
            "price_rule": "purchase<=3000: sale=x1.40; purchase>3000: sale=x1.26; old=x1.80; minimum=x1.20",
        }

    def _warn(self, message):
        self.report["warning_count"] += 1
        if len(self.report["warnings"]) < 200:
            self.report["warnings"].append(str(message)[:500])

    def _error(self, sku, exc):
        self.report["error_count"] += 1
        if len(self.report["errors"]) < 200:
            self.report["errors"].append({"sku": str(sku), "message": str(exc)[:500]})

    def _category_chain(self, source_id, categories):
        out = []
        seen = set()
        current = str(source_id or "").strip()
        while current and current not in seen:
            seen.add(current)
            row = categories.get(current)
            if not row:
                break
            out.append(row)
            current = str(row.get("parent_id") or "").strip()
        out.reverse()
        return out

    def _ensure_category(self, source_id, categories):
        source_id = str(source_id or "").strip()
        if source_id in self.category_cache:
            return self.category_cache[source_id]
        chain = self._category_chain(source_id, categories)
        if not chain:
            raise RuntimeError(f"missing Komus category {source_id}")
        parent = ""
        for src in chain:
            sid = str(src.get("id") or "").strip()
            if sid in self.category_cache:
                parent = self.category_cache[sid]
                continue
            title = str(src.get("title") or "").strip()
            matches = list(self.category_index.get((parent, _norm(title)), []))
            if len(matches) > 1:
                matches = sorted(matches, key=lambda x: str(x.get("id") or ""))
                self._warn(f"ambiguous KIT category reused: {title}")
            if matches:
                category_id = str(matches[0].get("id") or "").strip()
            elif self.dry_run:
                category_id = "dry-komus-category-" + sid
                created_row = {
                    "id": category_id,
                    "title": title,
                    "parent_id": parent,
                }
                self.kit_categories.append(created_row)
                self.category_index.setdefault((parent, _norm(title)), []).append(created_row)
            else:
                created = self.kit.create_category(title, parent or None)
                category_id = str(created.get("id") or "").strip()
                if not category_id:
                    raise RuntimeError(f"KIT category create failed: {title}")
                normalized = dict(created)
                normalized.setdefault("parent_id", parent)
                self.kit_categories.append(normalized)
                self.category_index.setdefault((parent, _norm(title)), []).append(normalized)
            self.category_cache[sid] = category_id
            parent = category_id
        self.category_cache[source_id] = parent
        return parent

    def _find_characteristic(self, title):
        candidates = list(self.characteristic_index.get(_norm(title), []))
        if not candidates:
            return None
        preferred = [
            x for x in candidates
            if str(x.get("type") or "").strip().upper() in ("STRING", "MULTIPLE_STRING")
        ]
        return sorted(preferred or candidates, key=lambda x: str(x.get("id") or ""))[0]

    def _ensure_characteristics(self, values_by_title):
        result = []
        for title, values in values_by_title:
            title = str(title or "").strip()
            clean = []
            for value in values:
                value = str(value or "").strip()
                if value and value not in clean:
                    clean.append(value)
            if not title or not clean:
                continue

            existing = self._find_characteristic(title)
            if existing:
                char_id = str(existing.get("id") or "").strip()
                char_type = str(existing.get("type") or "STRING").strip().upper()
            elif self.dry_run:
                char_id = "dry-komus-char-" + str(len(self.kit_characteristics) + 1)
                char_type = "STRING"
                created_row = {
                    "id": char_id,
                    "title": title,
                    "type": char_type,
                    "select_mode": "SINGLE",
                }
                self.kit_characteristics.append(created_row)
                self.characteristic_index.setdefault(_norm(title), []).append(created_row)
            else:
                created = self.kit.create_characteristic(title, "STRING", "SINGLE")
                char_id = str(created.get("id") or "").strip()
                if not char_id:
                    self._warn(f"KIT characteristic create failed: {title}")
                    continue
                char_type = str(created.get("type") or "STRING").strip().upper()
                self.kit_characteristics.append(created)
                self.characteristic_index.setdefault(_norm(title), []).append(created)

            if char_type == "MULTIPLE_STRING":
                result.append({
                    "characteristic_id": char_id,
                    "value": clean[0],
                    "values": clean,
                })
            else:
                joined = "; ".join(clean)
                result.append({
                    "characteristic_id": char_id,
                    "value": joined,
                    "values": [joined],
                })
        return result

    def _characteristic_values(self, offer, price_row):
        out = []
        for title, values in (offer.get("params") or {}).items():
            out.append((title, values))
        extras = [
            ("Артикул поставщика", [offer.get("art")]),
            ("Код производителя", [offer.get("vendor_code") or price_row.get("Код производителя")]),
            ("Модель товара", [offer.get("model")]),
            ("Штрихкод", offer.get("barcodes") or []),
            ("ТН ВЭД", [offer.get("tnved") or price_row.get("Код ТНВЭД")]),
            ("Страна происхождения", [offer.get("country") or price_row.get("Страна-производитель")]),
            ("НДС", [offer.get("vat") or price_row.get("НДС")]),
            ("Вес, кг", [price_row.get("Вес, кг")]),
            ("Объем, л", [price_row.get("Объем, л")]),
            ("Ед.изм", [price_row.get("Ед.изм")]),
            ("Схема вложения упаковки", [price_row.get("Схема вложения упаковки")]),
            ("Оптовая кратн. упаковки", [price_row.get("Оптовая кратн. упаковки")]),
            ("Тип упаковки", [price_row.get("Тип упаковки")]),
            ("Статус товара", [price_row.get("Статус товара")]),
            ("Ценовой сегмент", [price_row.get("Ценовой сегмент")]),
            ("Минпромторг", [price_row.get("Минпромторг")]),
        ]
        out.extend(extras)
        return out

    def _prepare_media(self, offer):
        if self.dry_run:
            return []
        media = []
        for url in offer.get("images") or []:
            try:
                suffix = os.path.splitext(urlparse(url).path)[1] or ".jpg"
                with tempfile.TemporaryDirectory(prefix="komus-img-") as td:
                    local = os.path.join(td, "image" + suffix[:10])
                    self.http.download_to_file(url, local)
                    uploaded = self.kit.upload_image(local)
                    image_id = str(uploaded.get("id") or "").strip()
                    if not image_id:
                        raise RuntimeError("KIT did not return image file id")
                    media.append({
                        "type": "IMAGE",
                        "display_sequence": len(media),
                        "image_id": image_id,
                    })
            except Exception as exc:
                self._warn(f"image skipped for {offer.get('sku')}: {exc}")
        return media

    def _purchase_price(self, offer, price_row):
        special = _money(price_row.get("Спец.ЦЕНА"))
        if special and special > 0:
            self.report["special_price_used"] += 1
            return special
        base = _money(offer.get("base_price"))
        if base and base > 0:
            self.report["base_price_fallback"] += 1
            return base
        return None

    def _price_update(self, purchase, variant):
        prices = calculate_prices(purchase)
        if not prices:
            return None
        pricing = variant.get("pricing") or {}
        if (
            _kit_money(pricing.get("price")) == _kit_money(prices.old)
            and _kit_money(pricing.get("manual_discount_price")) == _kit_money(prices.sale)
        ):
            return None
        variant_id = str(variant.get("id") or "").strip()
        if not variant_id:
            return None
        return {
            "variant_id": variant_id,
            "price": f"{prices.old:.2f}",
            "manual_discount_price": f"{prices.sale:.2f}",
        }

    def _stock_update(self, quantity, variant, warehouse_id):
        if _current_stock(variant, warehouse_id) == int(quantity):
            return None
        variant_id = str(variant.get("id") or "").strip()
        if not variant_id:
            return None
        return {
            "variant_id": variant_id,
            "warehouse_id": str(warehouse_id),
            "quantity": int(quantity),
        }

    def _new_payload(self, offer, purchase, product_id, warehouse_id, characteristics, media):
        prices = calculate_prices(purchase)
        payload = {
            "sku": offer["sku"],
            "name": offer["name"],
            "description": offer.get("description") or "",
            "status": "PUBLISHED",
            "product_id": str(product_id),
            "stocks": [{
                "warehouse_id": str(warehouse_id),
                "quantity": int(offer["quantity"]),
                "reserved": 0,
            }],
        }
        if offer.get("brand"):
            payload["brand"] = offer["brand"]
        if characteristics:
            payload["characteristics"] = characteristics
        if media:
            payload["media"] = media
        if prices:
            payload["pricing"] = {
                "price": f"{prices.old:.2f}",
                "manual_discount_price": f"{prices.sale:.2f}",
            }
        return payload

    def run(self):
        special_prices = load_special_prices(self.price_csv_path)
        self.report["price_rows"] = len(special_prices)
        source_categories = load_categories(self.xml_path)

        warehouse_id = self.kit.resolve_warehouse_exact(TARGET_WAREHOUSE)
        kit_index, duplicates = _index_komus_variants(self.kit)
        self.report["duplicate_kit_skus"] = len(duplicates)
        self.kit_categories = self.kit.list_categories()
        self.kit_characteristics = self.kit.list_characteristics()
        self.category_index = {}
        for row in self.kit_categories:
            key = (str(row.get("parent_id") or ""), _norm(row.get("title")))
            self.category_index.setdefault(key, []).append(row)
        self.characteristic_index = {}
        for row in self.kit_characteristics:
            self.characteristic_index.setdefault(_norm(row.get("title")), []).append(row)

        seen_eligible = set()
        price_batch = []
        stock_batch = []
        processed = 0
        complete = True
        source_pos = 0

        try:
            for offer in iter_all_offers(self.xml_path, special_prices, source_categories):
                if self.only_art and offer["art"] != self.only_art:
                    source_pos += 1
                    continue

                if not self.only_art and source_pos < self.skip_items:
                    source_pos += 1
                    continue

                if self.max_items is not None and processed >= self.max_items:
                    self.report["next_offset"] = source_pos
                    complete = False
                    break

                quantity = int(offer.get("quantity") or 0)
                if quantity <= 0:
                    source_pos += 1
                    processed += 1
                    self.report["source_rows_seen"] += 1
                    self.report["next_offset"] = source_pos
                    continue

                self.report["eligible_in_stock"] += 1
                sku_key = offer["sku"].casefold()
                price_row = special_prices.get(offer["art"]) or {}
                purchase = self._purchase_price(offer, price_row)

                if sku_key in duplicates:
                    seen_eligible.add(sku_key)
                    self._warn(f"duplicate KIT SKU skipped: {offer['sku']}")
                    source_pos += 1
                    processed += 1
                    self.report["source_rows_seen"] += 1
                    self.report["next_offset"] = source_pos
                    continue

                variant = kit_index.get(sku_key)

                if variant is None and self.max_new is not None and self.report["new_products_created"] >= self.max_new:
                    if self.new_only:
                        self.report["next_offset"] = source_pos
                        complete = False
                        break
                    self.report["new_limit_skipped"] += 1
                    seen_eligible.add(sku_key)
                    source_pos += 1
                    processed += 1
                    self.report["source_rows_seen"] += 1
                    self.report["next_offset"] = source_pos
                    continue

                seen_eligible.add(sku_key)

                if variant is not None:
                    self.report["existing_variants_seen"] += 1
                    if not self.new_only:
                        if purchase:
                            update = self._price_update(purchase, variant)
                            if update:
                                price_batch.append(update)
                                self.report["price_changes"] += 1
                        stock = self._stock_update(quantity, variant, warehouse_id)
                        if stock:
                            stock_batch.append(stock)
                            self.report["stock_changes"] += 1
                else:
                    if not purchase:
                        self._error(offer["sku"], "missing purchase price")
                    else:
                        try:
                            category_id = self._ensure_category(offer.get("category_id"), source_categories)
                            chars = self._ensure_characteristics(self._characteristic_values(offer, price_row))
                            media = self._prepare_media(offer)
                            if self.dry_run:
                                self._new_payload(offer, purchase, "dry-product", warehouse_id, chars, media)
                                self.report["new_products_created"] += 1
                            else:
                                product = self.kit.create_product(category_id)
                                product_id = str(product.get("id") or "").strip()
                                if not product_id:
                                    raise RuntimeError("KIT did not return product id")
                                payload = self._new_payload(offer, purchase, product_id, warehouse_id, chars, media)
                                created = self.kit.create_variant(payload)
                                variant_id = str(created.get("id") or "").strip()
                                if not variant_id:
                                    raise RuntimeError("KIT did not return variant id")
                                normalized = dict(payload)
                                normalized.update(created)
                                kit_index[sku_key] = normalized
                                self.report["new_products_created"] += 1
                        except Exception as exc:
                            self._error(offer["sku"], exc)

                if len(price_batch) >= 1000 and not self.dry_run:
                    self.kit.bulk_update_prices(price_batch)
                    price_batch = []
                if len(stock_batch) >= 1000 and not self.dry_run:
                    self.kit.bulk_update_stocks(stock_batch)
                    stock_batch = []

                source_pos += 1
                processed += 1
                self.report["source_rows_seen"] += 1
                self.report["next_offset"] = source_pos

            else:
                complete = True
        except Exception as exc:
            self._error("CATALOG", exc)
            complete = False

        if price_batch and not self.dry_run:
            self.kit.bulk_update_prices(price_batch)
        if stock_batch and not self.dry_run:
            self.kit.bulk_update_stocks(stock_batch)

        full_scope = (
            not self.only_art
            and self.skip_items == 0
            and self.max_items is None
            and complete
        )
        self.report["catalog_complete"] = bool(full_scope)

        if full_scope and not self.new_only:
            zero_updates = []
            for sku_key, variant in kit_index.items():
                if sku_key in seen_eligible or sku_key in duplicates:
                    continue
                if _current_stock(variant, warehouse_id) == 0:
                    continue
                variant_id = str(variant.get("id") or "").strip()
                if variant_id:
                    zero_updates.append({
                        "variant_id": variant_id,
                        "warehouse_id": str(warehouse_id),
                        "quantity": 0,
                    })
            self.report["absent_to_zero"] = len(zero_updates)
            if zero_updates and not self.dry_run:
                self.kit.bulk_update_stocks(zero_updates)

        self.report["status"] = (
            "ok" if not self.report["errors"]
            else ("degraded" if self.report["source_rows_seen"] or self.only_art else "failed")
        )
        return self.report


def main():
    parser = argparse.ArgumentParser(description="Komus personal catalog -> Yandex KIT")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-items", type=int, default=0)
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--max-new", type=int, default=None)
    parser.add_argument("--new-only", action="store_true")
    parser.add_argument("--only-art", default=None)
    parser.add_argument("--report", default="komus-kit/last_sync_report.json")
    args = parser.parse_args()

    report = None
    with tempfile.TemporaryDirectory(prefix="komus-personal-") as td:
        try:
            source = KomusPersonalSource(
                os.environ.get("KOMUS_LOGIN"),
                os.environ.get("KOMUS_PASSWORD"),
            )
            xml_path, price_path = source.download(td)

            http = SafeSession(max_attempts=8, timeout=(15, 120))
            kit = KitClient(
                os.environ["YANDEX_KIT_TOKEN"],
                http,
                min_request_interval=0.25,
            )
            report = KomusSyncRunner(
                xml_path,
                price_path,
                kit,
                http,
                dry_run=args.dry_run,
                skip_items=args.skip_items,
                max_items=args.max_items,
                max_new=args.max_new,
                new_only=args.new_only,
                only_art=args.only_art,
            ).run()
        except Exception as exc:
            report = {
                "status": "failed",
                "dry_run": args.dry_run,
                "error_count": 1,
                "errors": [{"sku": "BOOTSTRAP", "message": str(exc)[:1000]}],
            }

    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    compact_keys = [
        "status", "dry_run", "catalog_complete", "source_rows_seen",
        "eligible_in_stock", "price_rows", "special_price_used",
        "base_price_fallback", "existing_variants_seen", "new_products_created",
        "new_limit_skipped", "price_changes", "stock_changes",
        "absent_to_zero", "duplicate_kit_skus", "error_count",
        "warning_count", "skip_items", "next_offset", "max_new",
        "new_only", "only_art",
    ]
    print(json.dumps({k: report.get(k) for k in compact_keys}, ensure_ascii=False, indent=2))
    if report.get("status") == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
