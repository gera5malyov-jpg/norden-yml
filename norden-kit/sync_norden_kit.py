#!/usr/bin/env python3
import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from urllib.parse import urlparse

import requests

KIT_BASE = "https://api.kit.yandex.net"
NORDEN_API = "https://norden.group/api-products/"
NORDEN_SHORT_API = "https://norden.group/api-products-short/"
NORDEN_CATEGORIES_API = "https://norden.group/api-categories/"
FULL_XML_URL = "https://norden.group/index.php?dispatch=sw_user_prices.get_file&file=Norden.xml"
PRICE_XML_URL = "https://norden.group/index.php?dispatch=sw_user_prices.get_file&file=Norden.group+-K8%25.xml"

ROOT = Path(__file__).resolve().parent
MAPPING_PATH = ROOT / "kit_mapping.json"
REPORT_PATH = ROOT / "last_sync_report.json"
EXISTING_SKUS_PATH = ROOT / "existing_norden_skus.txt"
EXISTING_CODES_SEED_PATH = ROOT / "existing_norden_codes_seed.txt"

CODE_SITE_TITLE = "Код для сайта"
ARTICLE_TITLE = "Артикул"
NORDEN_CODE_TITLE = "Код Norden"
BRAND = "Norden"
WAREHOUSES = ("МСК", "СПБ привозной")

TECHNICAL_XML_TAGS = {
    "Ссылка", "Код", "Наименование", "Группа", "Артикул",
    "ВесЕдиницаИзмерения", "ВесЗнаменатель", "ВесИспользовать",
    "ВесМожноУказыватьВДокументах", "ВесЧислитель", "ВестиУчетПоГТД",
    "ВидНоменклатуры", "ЕдиницаИзмерения", "ДлинаЕдиницаИзмерения",
    "ДлинаЗнаменатель", "ДлинаИспользовать", "ДлинаМожноУказыватьВДокументах",
    "ДлинаЧислитель", "КодДляПоиска", "Марка", "НаборУпаковок",
    "НаименованиеПолное", "ОбъемДАЛ", "Производитель", "СтавкаНДС",
    "ТипНоменклатуры", "ОбъемЕдиницаИзмерения", "СезоннаяГруппа",
    "КоллекцияНоменклатуры", "АртикулДляПоиска", "ОбъемЕдиницаИзмерения1",
    "ОбъемЗнаменатель", "ОбъемИспользовать", "ОбъемЧислитель",
}

FRIENDLY_TITLES = {
    "Вескг": "Вес, кг",
    "Длинасм": "Длина, см",
    "Ширинасм": "Ширина, см",
    "Высотасм": "Высота, см",
    "РазмерупаковкиДШВ": "Размер упаковки Д×Ш×В",
    "Материалкрестовины": "Материал крестовины",
    "Материалкаркаса": "Материал каркаса",
    "Механизмкачания": "Механизм качания",
    "Цветкрестовины": "Цвет крестовины",
    "Цветкаркаса": "Цвет каркаса",
    "Сиденьематериал": "Материал сиденья",
    "Сиденьецвет": "Цвет сиденья",
    "Сиденьенаполнение": "Наполнение сиденья",
    "Подлокотникиматериал": "Материал подлокотников",
    "Подлокотникицвет": "Цвет подлокотников",
    "Подлокотникрегулировка": "Регулировка подлокотников",
    "Спинкаматериал": "Материал спинки",
    "Спинкацвет": "Цвет спинки",
    "Спинкарегулировка": "Регулировка спинки",
    "Подголовникналичие": "Наличие подголовника",
    "Подголовникрегулировка": "Регулировка подголовника",
    "Особенностимодели": "Особенности модели",
    "Высотакресламинимум": "Высота кресла минимум, см",
    "Высотакресламаксимум": "Высота кресла максимум, см",
    "Ширинакресла": "Ширина кресла, см",
    "Глубинакресла": "Глубина кресла, см",
    "Высотаспинки": "Высота спинки, см",
    "Глубинасиденья": "Глубина сиденья, см",
    "Ширинасиденья": "Ширина сиденья, см",
    "Высотаотполадосиденьямин": "Высота от пола до сиденья минимум, см",
    "Высотаотполадосиденьямакс": "Высота от пола до сиденья максимум, см",
    "Высотаотполадоподлокотникамин": "Высота от пола до подлокотника минимум, см",
    "Высотаотполадоподлокотникамакс": "Высота от пола до подлокотника максимум, см",
    "Регулировкасиденияпоглубине": "Регулировка сиденья по глубине",
    "Диаметркрестовины": "Диаметр крестовины, см",
}

CONFUSABLES = str.maketrans({
    "а":"a","в":"b","с":"c","е":"e","н":"h","к":"k","м":"m","о":"o","р":"p","т":"t","х":"x","у":"y",
    "А":"a","В":"b","С":"c","Е":"e","Н":"h","К":"k","М":"m","О":"o","Р":"p","Т":"t","Х":"x","У":"y",
})


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def s(value):
    return str(value or "").strip()


def norm_title(value):
    return re.sub(r"[^0-9a-zа-яё]+", "", unicodedata.normalize("NFKC", s(value)).casefold())


def norm_code(value):
    x = unicodedata.normalize("NFKC", s(value)).translate(CONFUSABLES).casefold()
    return re.sub(r"[^0-9a-z]+", "", x)


def dec(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None


def ceil_rub(value):
    d = dec(value)
    if d is None:
        return None
    return int(d.quantize(Decimal("1"), rounding=ROUND_CEILING))


def price_set(purchase):
    p = dec(purchase)
    if p is None:
        return None
    return {
        "sale": ceil_rub(p * Decimal("1.26")),
        "old": ceil_rub(p * Decimal("1.80")),
        "minimum": ceil_rub(p * Decimal("1.20")),
    }


def parse_stock(value):
    x = s(value).replace("\xa0", " ")
    if not x:
        return None
    if x.startswith(">"):
        nums = re.findall(r"\d+", x)
        if nums and int(nums[0]) >= 99:
            return 100
    try:
        return max(0, int(float(x.replace(" ", "").replace(",", "."))))
    except Exception:
        return None


class HttpError(RuntimeError):
    def __init__(self, status, url, body):
        super().__init__(f"HTTP {status} {url}: {body[:600]}")
        self.status = status
        self.url = url
        self.body = body


class KitClient:
    def __init__(self, token):
        token = s(token)
        if not token:
            raise RuntimeError("YANDEX_KIT_TOKEN is not configured")
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self.session = requests.Session()

    def request(self, method, path, *, params=None, body=None, files=None, timeout=120):
        url = KIT_BASE + path
        for attempt in range(12):
            headers = dict(self.headers)
            if body is not None and files is None:
                headers["Content-Type"] = "application/json"
            r = self.session.request(method, url, params=params, json=body if files is None else None,
                                     files=files, headers=headers, timeout=timeout)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After") or min(20, 2 ** attempt)))
                continue
            if r.status_code >= 500:
                time.sleep(min(20, 2 ** attempt))
                continue
            if r.status_code >= 400:
                raise HttpError(r.status_code, r.url, r.text)
            if not r.content:
                return {}
            try:
                return r.json()
            except Exception:
                return {"raw": r.text}
        raise RuntimeError(f"KIT retries exhausted: {method} {path}")

    @staticmethod
    def items(payload):
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        for key in ("items", "results", "variants", "warehouses", "categories", "characteristics", "products"):
            v = payload.get(key)
            if isinstance(v, list):
                return v
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
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

    def iter_collection(self, path, params=None):
        page = 1
        seen = 0
        while True:
            q = dict(params or {})
            q.update({"page": page, "per_page": 100})
            payload = self.request("GET", path, params=q)
            rows = self.items(payload)
            for row in rows:
                if isinstance(row, dict):
                    seen += 1
                    yield row
            total = self.total(payload)
            if not rows or (total is not None and seen >= total) or (total is None and len(rows) < 100):
                break
            page += 1

    def warehouses(self):
        return list(self.iter_collection("/v1/warehouses", {"status": "ACTIVE"}))

    def categories(self):
        return list(self.iter_collection("/v1/categories", {"status": ["ACTIVE"]}))

    def characteristics(self):
        return list(self.iter_collection("/v1/characteristics", {"status": ["ACTIVE"]}))

    def variants(self):
        return self.iter_collection("/v1/variants")

    def variants_parallel(self, workers=6):
        first = self.request("GET", "/v1/variants", params={"page": 1, "per_page": 100})
        first_rows = self.items(first)
        total = self.total(first)
        if total is None or total <= len(first_rows):
            for row in first_rows:
                if isinstance(row, dict):
                    yield row
            return
        pages = max(1, math.ceil(total / 100))
        for row in first_rows:
            if isinstance(row, dict):
                yield row
        def fetch_page(page):
            payload = self.request("GET", "/v1/variants", params={"page": page, "per_page": 100})
            return page, self.items(payload)
        with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
            futures = [pool.submit(fetch_page, page) for page in range(2, pages + 1)]
            done = 1
            for fut in as_completed(futures):
                page, rows = fut.result()
                done += 1
                if done % 25 == 0 or done == pages:
                    print(f"KIT mapping scan: {done}/{pages} pages", flush=True)
                for row in rows:
                    if isinstance(row, dict):
                        yield row

    def get_variant(self, variant_id):
        return self.request("GET", f"/v1/variants/{variant_id}")

    def create_category(self, title, parent_id=None):
        body = {"title": title}
        if parent_id:
            body["parent_id"] = parent_id
        return self.request("POST", "/v1/categories", body=body)

    def create_characteristic(self, title):
        return self.request("POST", "/v1/characteristics",
                            body={"title": title, "type": "STRING", "select_mode": "SINGLE"})

    def create_product(self, category_id):
        return self.request("POST", "/v1/products", body={"category_ids": [category_id]})

    def create_variant(self, body):
        return self.request("POST", "/v1/variants", body=body)

    def patch_variant(self, variant_id, body):
        url = f"/v1/variants/{variant_id}"
        full = KIT_BASE + url
        for attempt in range(12):
            headers = dict(self.headers)
            headers["Content-Type"] = "application/merge-patch+json"
            r = self.session.patch(full, json=body, headers=headers, timeout=120)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After") or min(20, 2 ** attempt)))
                continue
            if r.status_code >= 500:
                time.sleep(min(20, 2 ** attempt))
                continue
            if r.status_code >= 400:
                raise HttpError(r.status_code, r.url, r.text)
            return r.json() if r.content else {}
        raise RuntimeError("KIT patch retries exhausted")

    def upload_image_url(self, url):
        r = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        name = os.path.basename(urlparse(url).path) or "image.jpg"
        mime = r.headers.get("Content-Type") or "image/jpeg"
        files = {"file": (name, r.content, mime)}
        return self.request("POST", "/v1/files", files=files, timeout=180)

    def bulk_stocks(self, items):
        for start in range(0, len(items), 5000):
            self.request("POST", "/v1/variants/stocks/bulk_update",
                         body={"items": items[start:start+5000]})

    def bulk_prices(self, items, minimum_field=None):
        for start in range(0, len(items), 5000):
            batch = []
            for row in items[start:start+5000]:
                x = {
                    "variant_id": row["variant_id"],
                    "price": str(row["old"]),
                    "manual_discount_price": str(row["sale"]),
                }
                if minimum_field:
                    x[minimum_field] = str(row["minimum"])
                batch.append(x)
            self.request("POST", "/v1/variants/prices/bulk_update", body={"items": batch})

    def discover_minimum_price_field(self, sample):
        if not sample:
            return None
        candidates = ("minimum_price", "min_price", "minimum_sale_price", "manual_minimum_price")
        for field in candidates:
            try:
                self.bulk_prices([sample], minimum_field=field)
                return field
            except HttpError as exc:
                if exc.status not in (400, 404, 409, 422):
                    raise
        self.bulk_prices([sample], minimum_field=None)
        return None


def download_bytes(url):
    r = requests.get(url, timeout=180, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.content


def category_paths_from_api(secret):
    headers = {"secret": secret} if secret else {}
    r = requests.get(NORDEN_CATEGORIES_API, headers=headers, timeout=60)
    r.raise_for_status()
    rows = r.json()
    by_id = {s(x.get("category_id")): x for x in rows if isinstance(x, dict)}
    cache = {}

    def chain(cid):
        cid = s(cid)
        if cid in cache:
            return cache[cid]
        out, seen = [], set()
        cur = cid
        while cur and cur not in seen and cur in by_id:
            seen.add(cur)
            row = by_id[cur]
            title = s(row.get("name"))
            if title:
                out.append(title)
            p = s(row.get("parent_id"))
            cur = "" if p in ("", "0") else p
        out.reverse()
        cache[cid] = out
        return out
    return chain


def api_get_pages(url, secret, *, short=False):
    if not secret:
        raise RuntimeError("NORDEN_SECRET is empty")
    headers = {"secret": secret}
    page = 1
    out = []
    last_call = 0.0
    while True:
        delay = 6.2 - (time.monotonic() - last_call)
        if delay > 0 and page > 1:
            time.sleep(delay)
        r = requests.get(url, headers=headers, params={"page": page}, timeout=90)
        last_call = time.monotonic()
        if r.status_code >= 400:
            raise HttpError(r.status_code, r.url, r.text)
        data = r.json()
        batch = data.get("products") or []
        out.extend(batch)
        pd = data.get("page_data") or {}
        total = int(str(pd.get("total_items") or 0).replace(" ", "") or 0)
        per_page = int(str(pd.get("items_per_page") or len(batch) or 500))
        if not batch or len(out) >= total or len(batch) < per_page:
            break
        page += 1
    return out


def source_from_api(secret, *, short=False):
    rows = api_get_pages(NORDEN_SHORT_API if short else NORDEN_API, secret, short=short)
    chain = None
    if not short:
        try:
            chain = category_paths_from_api(secret)
        except Exception:
            chain = None
    products = {}
    duplicates = []
    for raw in rows:
        article = s(raw.get("product_code"))
        if not article:
            continue
        purchase = dec(raw.get("price"))
        stock = parse_stock(raw.get("qty"))
        if short:
            item = {"article": article, "purchase": purchase, "stock": stock, "source": "api-short"}
        else:
            cats = [s(x) for x in s(raw.get("category")).split(",") if s(x)]
            cat_path = []
            if chain and cats:
                paths = [chain(cid) for cid in cats]
                paths = [p for p in paths if p]
                if paths:
                    cat_path = max(paths, key=len)
            images = [s(x) for x in (raw.get("images") or []) if s(x)]
            chars = []
            for f in raw.get("features") or []:
                if isinstance(f, dict) and s(f.get("name")) and s(f.get("value")):
                    chars.append((s(f.get("name")), s(f.get("value"))))
            item = {
                "article": article,
                "norden_code": s(raw.get("Kod")),
                "name": s(raw.get("name")) or article,
                "description": s(raw.get("description")),
                "category_path": cat_path or ["Norden"],
                "purchase": purchase,
                "stock": stock,
                "images": images,
                "characteristics": chars,
                "source": "api",
            }
        if article in products:
            duplicates.append(article)
        products[article] = item
    return products, duplicates


def source_from_xml(*, short=False):
    price_root = ET.fromstring(download_bytes(PRICE_XML_URL))
    price_index = {}
    for n in price_root.iter("Номенклатура"):
        article = s(n.findtext("Артикул"))
        if not article:
            continue
        purchase = None
        for p in n.findall("Цена"):
            if s(p.attrib.get("ВидЦен")).casefold() == "опт":
                purchase = dec(p.text)
                break
        stock = None
        for st in n.findall("СвободныйОстаток"):
            if s(st.attrib.get("Склад")) == "Основной склад":
                stock = parse_stock(st.text)
                break
        price_index[article] = {"purchase": purchase, "stock": stock}
    if short:
        return {
            article: {"article": article, "purchase": row["purchase"], "stock": row["stock"], "source": "xml-price"}
            for article, row in price_index.items()
        }, []

    full_root = ET.fromstring(download_bytes(FULL_XML_URL))
    products = {}
    duplicates = []
    for n in full_root.iter("Номенклатура"):
        article = s(n.findtext("Артикул"))
        if not article:
            continue
        name = s(n.findtext("НаименованиеПолное")) or s(n.findtext("Наименование")) or article
        group = s(n.findtext("Группа"))
        parts = [x.strip() for x in group.split("///") if x.strip()]
        if not parts:
            parts = ["Norden"]
        elif parts[0].casefold() == "norden":
            parts[0] = "Norden"
        else:
            parts.insert(0, "Norden")
        description = s(n.findtext("Особенностимодели"))
        images = []
        chars = []
        for c in list(n):
            tag = s(c.tag)
            val = s(c.text)
            if not val:
                continue
            if tag.startswith("Ссылканафото"):
                images.append(val)
                continue
            if tag in TECHNICAL_XML_TAGS:
                continue
            title = FRIENDLY_TITLES.get(tag, tag)
            chars.append((title, val))
        chars.append((NORDEN_CODE_TITLE, s(n.findtext("Код"))))
        price = price_index.get(article, {})
        item = {
            "article": article,
            "norden_code": s(n.findtext("Код")),
            "name": name,
            "description": description,
            "category_path": parts,
            "purchase": price.get("purchase"),
            "stock": price.get("stock"),
            "images": list(dict.fromkeys(images)),
            "characteristics": [(a, b) for a, b in chars if s(a) and s(b)],
            "source": "xml",
        }
        if article in products:
            duplicates.append(article)
        products[article] = item
    return products, duplicates


def load_source(secret, *, short=False):
    api_error = None
    try:
        products, duplicates = source_from_api(secret, short=short)
        if products:
            return products, duplicates, "api-short" if short else "api", None
    except Exception as exc:
        api_error = str(exc)
    products, duplicates = source_from_xml(short=short)
    return products, duplicates, "xml-price" if short else "xml", api_error


def load_mapping():
    if not MAPPING_PATH.exists():
        return {"version": 1, "updated_at": None, "initial_complete": False, "variants": {}}
    try:
        data = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("variants"), dict):
            raise ValueError("bad mapping")
        return data
    except Exception:
        return {"version": 1, "updated_at": None, "initial_complete": False, "variants": {}}


def save_mapping(mapping):
    mapping["updated_at"] = now_iso()
    MAPPING_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def current_char_value(row, char_id):
    for c in row.get("characteristics") or []:
        if s(c.get("characteristic_id")) == char_id:
            vals = c.get("values") or []
            return s(c.get("value") or (vals[0] if vals else ""))
    return ""


def resolve_special_characteristics(kit):
    rows = kit.characteristics()
    by_title = defaultdict(list)
    for x in rows:
        by_title[norm_title(x.get("title"))].append(x)
    def one(title, create=False):
        matches = by_title.get(norm_title(title), [])
        if len(matches) == 1:
            return s(matches[0].get("id"))
        if len(matches) > 1:
            raise RuntimeError(f"Ambiguous KIT characteristic: {title}")
        if create:
            new = kit.create_characteristic(title)
            rows.append(new)
            by_title[norm_title(title)].append(new)
            return s(new.get("id"))
        return ""
    return rows, by_title, one(CODE_SITE_TITLE, True), one(ARTICLE_TITLE, True)


def match_source_article(code, source_articles):
    n = norm_code(code)
    if not n:
        return None
    exact = [a for a in source_articles if norm_code(a) == n]
    if len(exact) == 1:
        return exact[0]
    if len(n) >= 8:
        pref = [a for a in source_articles if norm_code(a).startswith(n) or n.startswith(norm_code(a))]
        if len(pref) == 1:
            return pref[0]
    return None


def seed_mapping_from_existing_codes(source, report):
    if not EXISTING_CODES_SEED_PATH.exists():
        return None
    seeds = [s(x) for x in EXISTING_CODES_SEED_PATH.read_text(encoding="utf-8").splitlines() if s(x)]
    if len(seeds) < 500:
        return None
    articles = list(source)
    mapping = {"version": 1, "updated_at": None, "initial_complete": False, "variants": {}}
    unresolved = []
    for code in seeds:
        article = match_source_article(code, articles)
        if not article:
            unresolved.append(code)
            continue
        mapping["variants"].setdefault(article, [])
    report["kit_mapping_method"] = "seed_existing_site_codes"
    report["kit_seed_codes"] = len(seeds)
    report["mapped_existing_articles"] = len(mapping["variants"])
    report["unresolved_existing_count"] = len(unresolved)
    report["unresolved_existing_sample"] = unresolved[:200]
    if len(mapping["variants"]) < 350:
        report["warnings"].append(
            f"Existing Norden seed mapped only {len(mapping['variants'])} of {len(seeds)} codes."
        )
        return None
    save_mapping(mapping)
    return mapping


def rebuild_mapping_from_sku_list(kit, source, code_site_id, report):
    articles = list(source)
    mapping = {"version": 1, "updated_at": None, "initial_complete": False, "variants": {}}
    skus = []
    if EXISTING_SKUS_PATH.exists():
        skus = [s(x) for x in EXISTING_SKUS_PATH.read_text(encoding="utf-8").splitlines() if s(x)]
    if len(skus) < 100:
        return None

    unresolved = []
    found_rows = []
    def fetch_one(sku):
        payload = kit.request("GET", "/v1/variants", params={"name": sku, "page": 1, "per_page": 100})
        rows = kit.items(payload)
        exact = [x for x in rows if s(x.get("sku")) == sku and s(x.get("brand")).casefold() == BRAND.casefold()]
        if len(exact) == 1:
            return sku, exact[0], None
        if len(exact) > 1:
            return sku, None, f"duplicate exact SKU matches: {len(exact)}"
        return sku, None, "not found"

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch_one, sku) for sku in skus]
        done = 0
        for fut in as_completed(futures):
            sku, row, error = fut.result()
            done += 1
            if done % 50 == 0 or done == len(futures):
                print(f"KIT targeted Norden mapping: {done}/{len(futures)}", flush=True)
            if row is None:
                if len(unresolved) < 200:
                    unresolved.append({"sku": sku, "reason": error})
                continue
            found_rows.append(row)

    for row in found_rows:
        code = current_char_value(row, code_site_id)
        article = match_source_article(code, articles)
        if not article:
            if len(unresolved) < 200:
                unresolved.append({
                    "sku": row.get("sku"), "kit_id": row.get("kit_id"),
                    "code_for_site": code, "name": row.get("name"),
                    "reason": "Code for site not found in Norden source",
                })
            continue
        mapping["variants"].setdefault(article, []).append({
            "variant_id": s(row.get("id")),
            "kit_id": row.get("kit_id"),
            "sku": s(row.get("sku")),
        })

    report["kit_mapping_method"] = "targeted_current_norden_skus"
    report["kit_target_skus"] = len(skus)
    report["kit_brand_norden_seen"] = len(found_rows)
    report["mapped_existing_articles"] = len(mapping["variants"])
    report["unresolved_existing_count"] = len(unresolved)
    report["unresolved_existing_sample"] = unresolved
    # Safety: the current export should resolve almost all existing Norden cards.
    if len(found_rows) < 500:
        report["warnings"].append(
            f"Targeted Norden lookup found only {len(found_rows)} of {len(skus)} cards; falling back to full scan."
        )
        return None
    save_mapping(mapping)
    return mapping


def rebuild_mapping(kit, source, code_site_id, report):
    articles = list(source)
    mapping = {"version": 1, "updated_at": None, "initial_complete": False, "variants": {}}
    scanned = 0
    norden_rows = 0
    unresolved = []
    for row in kit.variants_parallel(workers=6):
        scanned += 1
        if s(row.get("brand")).casefold() != BRAND.casefold():
            continue
        norden_rows += 1
        code = current_char_value(row, code_site_id)
        article = match_source_article(code, articles)
        if not article:
            if len(unresolved) < 200:
                unresolved.append({"sku": row.get("sku"), "kit_id": row.get("kit_id"), "code_for_site": code, "name": row.get("name")})
            continue
        mapping["variants"].setdefault(article, []).append({
            "variant_id": s(row.get("id")),
            "kit_id": row.get("kit_id"),
            "sku": s(row.get("sku")),
        })
    report["kit_variants_scanned"] = scanned
    report["kit_brand_norden_seen"] = norden_rows
    report["mapped_existing_articles"] = len(mapping["variants"])
    report["unresolved_existing_count"] = len(unresolved)
    report["unresolved_existing_sample"] = unresolved
    save_mapping(mapping)
    return mapping


def resolve_warehouses(kit):
    rows = kit.warehouses()
    result = {}
    for title in WAREHOUSES:
        matches = [s(x.get("id")) for x in rows if s(x.get("title")) == title and s(x.get("id"))]
        if len(matches) != 1:
            raise RuntimeError(f"Expected exactly one warehouse {title!r}, found {len(matches)}")
        result[title] = matches[0]
    return result


def ensure_category_path(kit, cache_rows, path):
    parent = ""
    path = [s(x) for x in path if s(x)]
    if not path:
        path = ["Norden"]
    for title in path:
        matches = [
            x for x in cache_rows
            if s(x.get("title")).casefold() == title.casefold()
            and s(x.get("parent_id") or "") == parent
        ]
        if len(matches) > 1:
            raise RuntimeError(f"Ambiguous category {title!r} under {parent!r}")
        if matches:
            cid = s(matches[0].get("id"))
        else:
            new = kit.create_category(title, parent or None)
            cid = s(new.get("id"))
            if not cid:
                raise RuntimeError(f"KIT did not return category id for {title!r}")
            cache_rows.append(new)
        parent = cid
    return parent


def characteristic_id(kit, all_rows, by_title, title):
    key = norm_title(title)
    matches = by_title.get(key, [])
    if len(matches) == 1:
        return s(matches[0].get("id"))
    if len(matches) > 1:
        # Prefer STRING if there is one unique string characteristic.
        string_matches = [x for x in matches if s(x.get("type")).upper() == "STRING"]
        if len(string_matches) == 1:
            return s(string_matches[0].get("id"))
        raise RuntimeError(f"Ambiguous KIT characteristic title: {title}")
    new = kit.create_characteristic(title)
    cid = s(new.get("id"))
    if not cid:
        raise RuntimeError(f"KIT did not return characteristic id for {title!r}")
    all_rows.append(new)
    by_title[key].append(new)
    return cid


def build_source_characteristics(item, kit, all_rows, by_title, code_site_id, article_id=None, new_article=None):
    out = [{"characteristic_id": code_site_id, "value": item["article"], "values": [item["article"]]}]
    for title, value in item.get("characteristics") or []:
        if not s(title) or not s(value):
            continue
        if norm_title(title) in (norm_title(CODE_SITE_TITLE), norm_title(ARTICLE_TITLE)):
            continue
        cid = characteristic_id(kit, all_rows, by_title, title)
        out.append({"characteristic_id": cid, "value": s(value), "values": [s(value)]})
    if article_id and new_article:
        out.append({"characteristic_id": article_id, "value": new_article, "values": [new_article]})
    # De-duplicate by characteristic id; last value wins.
    dedup = {}
    for x in out:
        dedup[x["characteristic_id"]] = x
    return list(dedup.values())


def fill_existing_content(kit, item, variant_id, all_chars, chars_by_title, code_site_id, report):
    try:
        current = kit.get_variant(variant_id)
    except Exception as exc:
        report["errors"].append({"article": item["article"], "stage": "get_existing", "message": str(exc)[:500]})
        return
    patch = {}
    if s(current.get("brand")) != BRAND:
        patch["brand"] = BRAND
    if not s(current.get("description")) and s(item.get("description")):
        patch["description"] = s(item["description"])

    existing = list(current.get("characteristics") or [])
    existing_values = {}
    for c in existing:
        cid = s(c.get("characteristic_id"))
        vals = c.get("values") or []
        existing_values[cid] = s(c.get("value") or (vals[0] if vals else ""))
    additions = []
    desired = build_source_characteristics(item, kit, all_chars, chars_by_title, code_site_id)
    for d in desired:
        cid = d["characteristic_id"]
        if not existing_values.get(cid):
            additions.append(d)
    if additions:
        merged = [x for x in existing if s(x.get("characteristic_id")) not in {a["characteristic_id"] for a in additions}]
        merged.extend(additions)
        patch["characteristics"] = merged
        report["empty_characteristics_filled"] += len(additions)

    if not (current.get("media") or []) and item.get("images"):
        media = []
        for url in item["images"][:20]:
            try:
                uploaded = kit.upload_image_url(url)
                fid = s(uploaded.get("id"))
                if fid:
                    media.append({"type": "IMAGE", "display_sequence": len(media), "image_id": fid})
            except Exception as exc:
                report["image_errors"] += 1
                if len(report["warnings"]) < 200:
                    report["warnings"].append(f"{item['article']}: image failed: {exc}")
        if media:
            patch["media"] = media
            report["existing_products_images_filled"] += 1

    if patch:
        try:
            kit.patch_variant(variant_id, patch)
            report["existing_products_patched"] += 1
        except Exception as exc:
            report["errors"].append({"article": item["article"], "stage": "patch_existing", "message": str(exc)[:500]})


def create_new_product(kit, item, categories, all_chars, chars_by_title, code_site_id, article_id, warehouses, report):
    category_id = ensure_category_path(kit, categories, item.get("category_path") or ["Norden"])
    product = kit.create_product(category_id)
    product_id = s(product.get("id"))
    if not product_id:
        raise RuntimeError("KIT did not return product id")

    safe_part = re.sub(r"[^0-9A-Za-z]+", "-", item["article"])[:45].strip("-") or "ITEM"
    suffix = hashlib.sha1(item["article"].encode("utf-8")).hexdigest()[:10]
    temp_sku = f"NORDEN-TMP-{safe_part}-{suffix}"

    chars = build_source_characteristics(item, kit, all_chars, chars_by_title, code_site_id)
    media = []
    for url in item.get("images") or []:
        if len(media) >= 20:
            break
        try:
            uploaded = kit.upload_image_url(url)
            fid = s(uploaded.get("id"))
            if fid:
                media.append({"type": "IMAGE", "display_sequence": len(media), "image_id": fid})
        except Exception as exc:
            report["image_errors"] += 1
            if len(report["warnings"]) < 200:
                report["warnings"].append(f"{item['article']}: image failed: {exc}")

    body = {
        "sku": temp_sku,
        "name": item["name"],
        "description": s(item.get("description")),
        "status": "PUBLISHED",
        "product_id": product_id,
        "brand": BRAND,
        "characteristics": chars,
    }
    if media:
        body["media"] = media
    stock = item.get("stock")
    if stock is not None:
        body["stocks"] = [
            {"warehouse_id": wid, "quantity": int(stock), "reserved": 0}
            for wid in warehouses.values()
        ]
    prices = price_set(item.get("purchase"))
    if prices:
        body["pricing"] = {
            "price": str(prices["old"]),
            "manual_discount_price": str(prices["sale"]),
        }

    created = kit.create_variant(body)
    variant_id = s(created.get("id"))
    if not variant_id:
        raise RuntimeError("KIT did not return variant id")
    kit_id = created.get("kit_id")
    if kit_id in (None, ""):
        created = kit.get_variant(variant_id)
        kit_id = created.get("kit_id")
    if kit_id in (None, ""):
        raise RuntimeError("KIT did not return kit_id for new variant")
    final_article = f"100-{kit_id}"

    final_chars = build_source_characteristics(
        item, kit, all_chars, chars_by_title, code_site_id, article_id=article_id, new_article=final_article
    )
    try:
        kit.patch_variant(variant_id, {"sku": final_article, "characteristics": final_chars})
    except Exception as exc:
        # Preserve the product and correct visible Article characteristic even if SKU patch is restricted.
        try:
            kit.patch_variant(variant_id, {"characteristics": final_chars})
        except Exception:
            pass
        if len(report["warnings"]) < 200:
            report["warnings"].append(f"{item['article']}: could not set SKU to {final_article}: {exc}")

    return {
        "variant_id": variant_id,
        "kit_id": kit_id,
        "sku": final_article,
    }


def planned_updates(mapping, source, warehouses):
    price_rows = []
    stock_rows = []
    mapped_articles = 0
    for article, variants in mapping.get("variants", {}).items():
        item = source.get(article)
        if not item:
            continue
        mapped_articles += 1
        ps = price_set(item.get("purchase"))
        for v in variants:
            vid = s(v.get("variant_id"))
            if not vid:
                continue
            if ps:
                price_rows.append({"variant_id": vid, **ps})
            if item.get("stock") is not None:
                for wid in warehouses.values():
                    stock_rows.append({"variant_id": vid, "warehouse_id": wid, "quantity": int(item["stock"])})
    return price_rows, stock_rows, mapped_articles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("full", "price-stock", "scheduled", "preflight"), default="full")
    ap.add_argument("--rebuild-map", action="store_true")
    ap.add_argument("--max-new", type=int, default=0)
    ap.add_argument("--time-budget-seconds", type=int, default=17000)
    args = ap.parse_args()

    report = {
        "started_at": now_iso(),
        "mode": args.mode,
        "source": None,
        "api_error": None,
        "source_products": 0,
        "source_duplicate_articles": 0,
        "mapped_existing_articles": 0,
        "planned_new_products": 0,
        "new_products_created": 0,
        "existing_products_patched": 0,
        "empty_characteristics_filled": 0,
        "existing_products_images_filled": 0,
        "image_errors": 0,
        "price_updates": 0,
        "stock_updates": 0,
        "minimum_price_field": None,
        "minimum_price_supported": False,
        "warnings": [],
        "errors": [],
        "initial_complete": False,
    }

    token = os.environ.get("YANDEX_KIT_TOKEN", "")
    secret = os.environ.get("NORDEN_SECRET", "").strip()
    kit = KitClient(token)
    warehouses = resolve_warehouses(kit)

    short = args.mode == "price-stock"
    source, duplicates, source_kind, api_error = load_source(secret, short=short)
    report["source"] = source_kind
    report["api_error"] = api_error
    report["source_products"] = len(source)
    report["source_duplicate_articles"] = len(set(duplicates))

    if len(source) < (100 if short else 1000):
        raise RuntimeError(f"Safety stop: unexpectedly small Norden source ({len(source)} products)")

    all_chars, chars_by_title, code_site_id, article_id = resolve_special_characteristics(kit)
    mapping = load_mapping()

    if args.rebuild_map or not mapping.get("variants"):
        if short:
            # Need the full article list for safe mapping.
            full_source, _, _, _ = load_source(secret, short=False)
            mapping = seed_mapping_from_existing_codes(full_source, report) if args.mode in ("preflight", "full") else None
            if mapping is None:
                mapping = rebuild_mapping_from_sku_list(kit, full_source, code_site_id, report)
            if mapping is None:
                mapping = rebuild_mapping(kit, full_source, code_site_id, report)
        else:
            mapping = seed_mapping_from_existing_codes(source, report) if args.mode in ("preflight", "full") else None
            if mapping is None:
                mapping = rebuild_mapping_from_sku_list(kit, source, code_site_id, report)
            if mapping is None:
                mapping = rebuild_mapping(kit, source, code_site_id, report)
    else:
        report["mapped_existing_articles"] = len(mapping.get("variants", {}))

    price_rows, stock_rows, mapped_articles = planned_updates(mapping, source, warehouses)
    report["mapped_source_articles_for_updates"] = mapped_articles

    # Price-stock mode never creates products.
    if args.mode == "price-stock":
        if price_rows:
            min_field = kit.discover_minimum_price_field(price_rows[0])
            report["minimum_price_field"] = min_field
            report["minimum_price_supported"] = bool(min_field)
            kit.bulk_prices(price_rows, minimum_field=min_field)
            report["price_updates"] = len(price_rows)
        if stock_rows:
            kit.bulk_stocks(stock_rows)
            report["stock_updates"] = len(stock_rows)
        report["initial_complete"] = bool(mapping.get("initial_complete"))
        report["unmapped_source_articles"] = len(set(source) - set(mapping.get("variants", {})))
        report["finished_at"] = now_iso()
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        save_mapping(mapping)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    # Preflight performs no writes other than local report/mapping files.
    missing = [a for a in source if a not in mapping.get("variants", {})]
    report["planned_new_products"] = len(missing)
    if args.mode == "preflight":
        report["initial_complete"] = len(missing) == 0
        report["finished_at"] = now_iso()
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        save_mapping(mapping)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    # Full/scheduled: update prices and stocks for all already mapped products first.
    if price_rows:
        min_field = kit.discover_minimum_price_field(price_rows[0])
        report["minimum_price_field"] = min_field
        report["minimum_price_supported"] = bool(min_field)
        kit.bulk_prices(price_rows, minimum_field=min_field)
        report["price_updates"] += len(price_rows)
    else:
        min_field = None
    if stock_rows:
        kit.bulk_stocks(stock_rows)
        report["stock_updates"] += len(stock_rows)

    # Create missing Norden products first so new cards appear in KIT immediately.
    categories = kit.categories()
    start = time.monotonic()
    max_new = args.max_new if args.max_new > 0 else None
    # Scheduled run only creates a conservative number if initial load was incomplete.
    if args.mode == "scheduled" and not mapping.get("initial_complete"):
        max_new = max_new or 300

    for article in missing:
        if max_new is not None and report["new_products_created"] >= max_new:
            break
        if time.monotonic() - start >= args.time_budget_seconds:
            report["warnings"].append("Time budget reached; remaining new products will continue on a later full/scheduled run.")
            break
        item = source[article]
        try:
            new = create_new_product(
                kit, item, categories, all_chars, chars_by_title, code_site_id, article_id, warehouses, report
            )
            mapping["variants"][article] = [new]
            save_mapping(mapping)
            report["new_products_created"] += 1

            ps = price_set(item.get("purchase"))
            if ps:
                prow = {"variant_id": new["variant_id"], **ps}
                kit.bulk_prices([prow], minimum_field=min_field)
                report["price_updates"] += 1
            if item.get("stock") is not None:
                srows = [
                    {"variant_id": new["variant_id"], "warehouse_id": wid, "quantity": int(item["stock"])}
                    for wid in warehouses.values()
                ]
                kit.bulk_stocks(srows)
                report["stock_updates"] += len(srows)
        except Exception as exc:
            report["errors"].append({"article": article, "stage": "create", "message": str(exc)[:800]})
            if len(report["errors"]) >= 200:
                report["warnings"].append("Error limit reached; stopping product creation.")
                break

    # Only after creating missing products, fill empty content/characteristics on existing cards.
    if args.mode == "full":
        for article, variants in list(mapping.get("variants", {}).items()):
            item = source.get(article)
            if not item:
                continue
            for v in variants:
                vid = s(v.get("variant_id"))
                if vid:
                    fill_existing_content(kit, item, vid, all_chars, chars_by_title, code_site_id, report)

    remaining = [a for a in source if a not in mapping.get("variants", {})]
    mapping["initial_complete"] = len(remaining) == 0
    report["remaining_new_products"] = len(remaining)
    report["initial_complete"] = mapping["initial_complete"]
    save_mapping(mapping)
    report["finished_at"] = now_iso()
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        failure = {
            "finished_at": now_iso(),
            "status": "failed",
            "message": str(exc),
        }
        try:
            REPORT_PATH.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        print(json.dumps(failure, ensure_ascii=False, indent=2), file=sys.stderr)
        raise
