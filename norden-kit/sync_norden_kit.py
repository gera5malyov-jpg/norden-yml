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
CATEGORY_POLICY_PATH = ROOT / "category_policy.json"
EXISTING_SKUS_PATH = ROOT / "existing_norden_skus.txt"
EXISTING_CODES_SEED_PATH = ROOT / "existing_norden_codes_seed.txt"

CODE_SITE_TITLE = "Код для сайта"
ARTICLE_TITLE = "Артикул"
NORDEN_CODE_TITLE = "Код Norden"
SELLER_CODE_TITLE = "Код продавца"
EXTERNAL_ID_TITLES = ("Внешний ID", "Внешний идентификатор", "External ID")
IDENTITY_CHARACTERISTIC_TITLES = (
    ARTICLE_TITLE,
    SELLER_CODE_TITLE,
    NORDEN_CODE_TITLE,
    *EXTERNAL_ID_TITLES,
    CODE_SITE_TITLE,
)
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


_CATEGORY_POLICY_CACHE = None


def load_category_policy():
    global _CATEGORY_POLICY_CACHE
    if _CATEGORY_POLICY_CACHE is None:
        if not CATEGORY_POLICY_PATH.exists():
            raise RuntimeError("Norden category policy file is missing")
        _CATEGORY_POLICY_CACHE = json.loads(CATEGORY_POLICY_PATH.read_text(encoding="utf-8"))
    return _CATEGORY_POLICY_CACHE


def approved_category_path(source_path):
    parts = [s(x) for x in (source_path or []) if s(x)]
    if not parts:
        return None
    source = " > ".join(parts)
    key = hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]
    policy = load_category_policy()
    rule = (policy.get("rules") or {}).get(key)
    if not rule:
        return None
    destinations = policy.get("destinations") or []
    try:
        target = destinations[int(rule[0])]
    except (IndexError, ValueError, TypeError):
        raise RuntimeError(f"Invalid Norden category rule for {source!r}")
    result = [s(x) for x in str(target).split(">") if s(x)]
    if not result:
        raise RuntimeError(f"Empty Norden target category for {source!r}")
    return result


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
    # KIT rejects zero/negative "price before discount".
    # Create/update the product without pricing until Norden provides a positive purchase price.
    if p is None or p <= 0:
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


def is_moscow_only_product(item):
    name = s((item or {}).get("name")).casefold()
    return "только" in name and ("москва" in name or "москве" in name)


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
        self.min_request_interval = 0.42
        self._last_request_at = 0.0

    def _pace(self):
        now = time.monotonic()
        delay = self.min_request_interval - (now - self._last_request_at)
        if delay > 0:
            time.sleep(delay)
        self._last_request_at = time.monotonic()

    def request(self, method, path, *, params=None, body=None, files=None, timeout=120):
        url = KIT_BASE + path
        for attempt in range(12):
            self._pace()
            headers = dict(self.headers)
            if body is not None and files is None:
                headers["Content-Type"] = "application/json"
            r = self.session.request(method, url, params=params, json=body if files is None else None,
                                     files=files, headers=headers, timeout=timeout)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After") or min(45, 5 * (attempt + 1))))
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
            self._pace()
            headers = dict(self.headers)
            headers["Content-Type"] = "application/merge-patch+json"
            r = self.session.patch(full, json=body, headers=headers, timeout=120)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After") or min(45, 5 * (attempt + 1))))
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

    def _bulk_with_missing_variant_retry(self, path, batch):
        pending = list(batch)
        skipped = []
        while pending:
            try:
                self.request("POST", path, body={"items": pending})
                return skipped
            except HttpError as exc:
                if exc.status != 400:
                    raise
                try:
                    payload = json.loads(exc.body)
                except Exception:
                    raise
                missing = {
                    s(row.get("variant_id"))
                    for row in (payload.get("errors") or [])
                    if isinstance(row, dict)
                    and s(row.get("code")) == "VARIANT_NOT_FOUND"
                    and s(row.get("variant_id"))
                }
                if not missing:
                    raise
                skipped.extend(sorted(missing))
                print(
                    f"KIT {path}: skip {len(missing)} stale variant ids and retry batch",
                    flush=True,
                )
                pending = [
                    row for row in pending
                    if s(row.get("variant_id")) not in missing
                ]
        return skipped

    def bulk_stocks(self, items):
        skipped = []
        for start in range(0, len(items), 5000):
            skipped.extend(self._bulk_with_missing_variant_retry(
                "/v1/variants/stocks/bulk_update",
                items[start:start+5000],
            ))
        return list(dict.fromkeys(skipped))

    def bulk_prices(self, items, minimum_field=None):
        skipped = []
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
            skipped.extend(self._bulk_with_missing_variant_retry(
                "/v1/variants/prices/bulk_update",
                batch,
            ))
        return list(dict.fromkeys(skipped))

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
    # Primary source: Norden API.
    # Fallback if API is unavailable/unauthorized:
    # - catalog/content/images/features from Norden.xml
    # - purchase price and stock from Norden.group -K8%.xml
    api_error = None
    try:
        products, duplicates = source_from_api(secret, short=short)
        if products:
            return products, duplicates, "api-short" if short else "api", None
    except Exception as exc:
        api_error = str(exc)
    products, duplicates = source_from_xml(short=short)
    return products, duplicates, "xml-price" if short else "xml-full+price", api_error


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


def resolve_identity_characteristic_ids(rows):
    """Resolve existing KIT identity characteristics without creating new ones."""
    by_title = defaultdict(list)
    for row in rows or []:
        cid = s(row.get("id"))
        title = s(row.get("title"))
        if cid and title:
            by_title[norm_title(title)].append(cid)

    result = {}
    for title in IDENTITY_CHARACTERISTIC_TITLES:
        ids = list(dict.fromkeys(by_title.get(norm_title(title), [])))
        if ids:
            result[title] = ids
    return result


def build_source_identity_index(source):
    """Index only hard supplier identifiers. Product name is intentionally excluded."""
    index = defaultdict(set)
    for article, item in source.items():
        for value in (article, (item or {}).get("norden_code")):
            key = norm_code(value)
            if key:
                index[key].add(article)
    return index


def match_source_by_identity(row, source_index, identity_char_ids):
    """Match KIT row to one Norden source item by SKU/Article/Code/External ID/Site code."""
    values = []
    sku = s(row.get("sku"))
    if sku:
        values.append(("SKU", sku))

    for title, ids in (identity_char_ids or {}).items():
        for cid in ids:
            value = current_char_value(row, cid)
            if value:
                values.append((title, value))

    candidates = defaultdict(list)
    for field, value in values:
        key = norm_code(value)
        if not key:
            continue
        for article in source_index.get(key, set()):
            candidates[article].append({"field": field, "value": value})

    if len(candidates) == 1:
        article = next(iter(candidates))
        return article, candidates[article], values, None
    if len(candidates) > 1:
        return None, [], values, dict(candidates)
    return None, [], values, None


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


def rebuild_mapping_from_sku_list(kit, source, identity_char_ids, report):
    mapping = {"version": 1, "updated_at": None, "initial_complete": False, "variants": {}}
    source_index = build_source_identity_index(source)
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

    match_field_counts = defaultdict(int)
    identity_conflicts = 0
    for row in found_rows:
        article, matched, values, conflict = match_source_by_identity(
            row, source_index, identity_char_ids
        )
        if not article:
            if conflict:
                identity_conflicts += 1
            if len(unresolved) < 200:
                unresolved.append({
                    "sku": row.get("sku"),
                    "kit_id": row.get("kit_id"),
                    "name": row.get("name"),
                    "identity_values": [{"field": a, "value": b} for a, b in values],
                    "identity_conflict": conflict,
                    "reason": "No unique hard-identifier match in Norden source",
                })
            continue
        for hit in matched:
            match_field_counts[hit["field"]] += 1
        mapping["variants"].setdefault(article, []).append({
            "variant_id": s(row.get("id")),
            "kit_id": row.get("kit_id"),
            "sku": s(row.get("sku")),
            "match_method": "hard_identifiers",
            "matched_identifiers": matched,
        })

    report["kit_mapping_method"] = "targeted_current_norden_skus_multi_id"
    report["kit_target_skus"] = len(skus)
    report["kit_brand_norden_seen"] = len(found_rows)
    report["mapped_existing_articles"] = len(mapping["variants"])
    report["identity_match_field_counts"] = dict(match_field_counts)
    report["identity_conflicts"] = identity_conflicts
    report["unresolved_existing_count"] = len(unresolved)
    report["unresolved_existing_sample"] = unresolved
    if len(found_rows) < 500:
        report["warnings"].append(
            f"Targeted Norden lookup found only {len(found_rows)} of {len(skus)} cards; falling back to full scan."
        )
        return None
    save_mapping(mapping)
    return mapping

def rebuild_mapping(kit, source, identity_char_ids, report):
    mapping = {"version": 1, "updated_at": None, "initial_complete": False, "variants": {}}
    source_index = build_source_identity_index(source)
    scanned = 0
    norden_rows = 0
    unresolved = []
    identity_conflicts = 0
    match_field_counts = defaultdict(int)

    for row in kit.variants_parallel(workers=6):
        scanned += 1
        if s(row.get("brand")).casefold() != BRAND.casefold():
            continue
        if s(row.get("status")).upper() == "ARCHIVED":
            continue
        norden_rows += 1

        article, matched, values, conflict = match_source_by_identity(
            row, source_index, identity_char_ids
        )
        if not article:
            if conflict:
                identity_conflicts += 1
            if len(unresolved) < 200:
                unresolved.append({
                    "sku": row.get("sku"),
                    "kit_id": row.get("kit_id"),
                    "name": row.get("name"),
                    "identity_values": [{"field": a, "value": b} for a, b in values],
                    "identity_conflict": conflict,
                    "reason": "No unique hard-identifier match; name matching disabled for Norden",
                })
            continue

        for hit in matched:
            match_field_counts[hit["field"]] += 1
        mapping["variants"].setdefault(article, []).append({
            "variant_id": s(row.get("id")),
            "kit_id": row.get("kit_id"),
            "sku": s(row.get("sku")),
            "match_method": "hard_identifiers",
            "matched_identifiers": matched,
        })

    report["kit_variants_scanned"] = scanned
    report["kit_brand_norden_seen"] = norden_rows
    report["mapped_existing_articles"] = len(mapping["variants"])
    report["identity_match_field_counts"] = dict(match_field_counts)
    report["identity_conflicts"] = identity_conflicts
    report["name_matching_policy"] = "NO"
    report["unresolved_existing_count"] = len(unresolved)
    report["unresolved_existing_sample"] = unresolved
    save_mapping(mapping)
    return mapping

def canonicalize_duplicate_mapping(kit, mapping, warehouses, report):
    """Keep one canonical live card per Norden article and quarantine duplicates."""
    duplicate_groups = []
    quarantine_rows = []
    for article, rows in list(mapping.get("variants", {}).items()):
        unique = {}
        for row in rows or []:
            vid = s(row.get("variant_id"))
            if vid:
                unique[vid] = row
        rows = list(unique.values())
        if len(rows) <= 1:
            mapping["variants"][article] = rows
            continue

        def key(row):
            sku = s(row.get("sku"))
            kit_id = row.get("kit_id")
            try:
                kid = int(kit_id)
            except Exception:
                kid = 10**18
            # Prefer legacy/original SKU (AF-*, supplier-era cards) over auto-created 100-*.
            return (1 if sku.startswith("100-") else 0, kid, sku)

        rows.sort(key=key)
        keep = rows[0]
        drop = rows[1:]
        mapping["variants"][article] = [keep]
        duplicate_groups.append({
            "article": article,
            "kept": keep,
            "quarantined": drop,
        })
        for row in drop:
            vid = s(row.get("variant_id"))
            for wid in warehouses.values():
                quarantine_rows.append({
                    "variant_id": vid,
                    "warehouse_id": wid,
                    "quantity": 0,
                })

    if quarantine_rows:
        stale = kit.bulk_stocks(quarantine_rows)
        report["quarantine_stale_variant_ids"] = stale
    report["duplicate_groups_quarantined"] = len(duplicate_groups)
    report["duplicate_variants_quarantined"] = sum(len(x["quarantined"]) for x in duplicate_groups)
    report["duplicate_groups_sample"] = duplicate_groups[:100]
    return duplicate_groups


def merge_recent_norden_mapping(kit, source, code_site_id, mapping, report, pages=10):
    """Merge recently created Norden cards into mapping so interrupted runs never duplicate them."""
    merged = 0
    repair = []
    known_ids = {
        s(v.get("variant_id"))
        for vs in mapping.get("variants", {}).values()
        for v in (vs or [])
        if s(v.get("variant_id"))
    }
    for page in range(1, pages + 1):
        payload = kit.request("GET", "/v1/variants", params={"page": page, "per_page": 100})
        rows = kit.items(payload)
        if not rows:
            break
        for row in rows:
            if s(row.get("brand")).casefold() != BRAND.casefold():
                continue
            code = current_char_value(row, code_site_id)
            article = match_source_article(code, list(source))
            if not article:
                continue
            vid = s(row.get("id"))
            if vid and vid not in known_ids:
                mapping["variants"].setdefault(article, []).append({
                    "variant_id": vid,
                    "kit_id": row.get("kit_id"),
                    "sku": s(row.get("sku")),
                })
                known_ids.add(vid)
                merged += 1
            if vid:
                # Revisit recent Norden cards safely. The repair function only:
                # - adds description when blank,
                # - adds completely absent characteristics,
                # - adds images only when media is empty.
                repair.append((article, vid))
        time.sleep(1.2)
    report["recent_norden_mapping_merged"] = merged
    report["recent_norden_repair_candidates"] = len(repair)
    save_mapping(mapping)
    return repair


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
        raise RuntimeError("Category path is required; automatic Norden fallback is disabled")
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

    # Persist supplier identity fields so future duplicate checks do not depend on names.
    seller_code_id = characteristic_id(kit, all_rows, by_title, SELLER_CODE_TITLE)
    out.append({
        "characteristic_id": seller_code_id,
        "value": item["article"],
        "values": [item["article"]],
    })
    if s(item.get("norden_code")):
        norden_code_id = characteristic_id(kit, all_rows, by_title, NORDEN_CODE_TITLE)
        out.append({
            "characteristic_id": norden_code_id,
            "value": s(item["norden_code"]),
            "values": [s(item["norden_code"])],
        })

    for title, value in item.get("characteristics") or []:
        if not s(title) or not s(value):
            continue
        if norm_title(title) in (norm_title(CODE_SITE_TITLE), norm_title(ARTICLE_TITLE)):
            continue
        try:
            cid = characteristic_id(kit, all_rows, by_title, title)
        except RuntimeError as exc:
            # One ambiguous/invalid characteristic must not block all other fields.
            # Skip only this characteristic; keep filling the rest.
            print(f"Skip Norden characteristic {title!r}: {exc}", flush=True)
            continue
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

    # 1. IMAGES FIRST. Do not let characteristic creation block media upload.
    if not (current.get("media") or []) and item.get("images"):
        media = []
        for url in item["images"][:20]:
            try:
                uploaded = kit.upload_image_url(url)
                fid = s(uploaded.get("id"))
                if fid:
                    media.append({"type": "IMAGE", "display_sequence": len(media), "image_id": fid})
                time.sleep(0.8)
            except Exception as exc:
                report["image_errors"] += 1
                if len(report["warnings"]) < 200:
                    report["warnings"].append(f"{item['article']}: image failed: {exc}")
        if media:
            try:
                kit.patch_variant(variant_id, {"media": media})
                verify = kit.get_variant(variant_id)
                verified = [
                    m for m in (verify.get("media") or [])
                    if isinstance(m, dict) and s(m.get("type")).upper() == "IMAGE"
                ]
                if verified:
                    report["existing_products_images_filled"] += 1
                else:
                    report["image_errors"] += 1
                    if len(report["warnings"]) < 200:
                        report["warnings"].append(
                            f"{item['article']}: KIT accepted media patch but verification returned no images"
                        )
            except Exception as exc:
                report["image_errors"] += 1
                if len(report["warnings"]) < 200:
                    report["warnings"].append(f"{item['article']}: media patch failed: {exc}")

    # Refresh after image patch so subsequent patch preserves current state.
    try:
        current = kit.get_variant(variant_id)
    except Exception:
        pass

    # 2. Brand/description/characteristics after media is safely attached.
    patch = {}
    if s(current.get("brand")) != BRAND:
        patch["brand"] = BRAND
    if not s(current.get("description")) and s(item.get("description")):
        patch["description"] = s(item["description"])

    existing = list(current.get("characteristics") or [])
    existing_ids = {
        s(c.get("characteristic_id"))
        for c in existing
        if s(c.get("characteristic_id"))
    }

    additions = []
    try:
        desired = build_source_characteristics(item, kit, all_chars, chars_by_title, code_site_id)
        for d in desired:
            cid = d["characteristic_id"]
            # Strict rule: never overwrite or refill an existing characteristic.
            # Add only characteristic IDs that are completely absent from the card.
            if cid not in existing_ids:
                additions.append(d)
    except Exception as exc:
        report["errors"].append({
            "article": item["article"],
            "stage": "build_characteristics",
            "message": str(exc)[:500],
        })

    if additions:
        merged = [
            x for x in existing
            if s(x.get("characteristic_id")) not in {a["characteristic_id"] for a in additions}
        ]
        merged.extend(additions)
        patch["characteristics"] = merged
        report["empty_characteristics_filled"] += len(additions)

    if patch:
        try:
            kit.patch_variant(variant_id, patch)
            report["existing_products_patched"] += 1
        except Exception as exc:
            report["errors"].append({
                "article": item["article"],
                "stage": "patch_existing",
                "message": str(exc)[:500],
            })



def create_new_product(kit, item, categories, all_chars, chars_by_title, code_site_id, article_id, warehouses, report):
    category_path = item.get("category_path") or []
    if not category_path:
        raise RuntimeError("Approved category path is required for Norden product creation")
    category_id = ensure_category_path(kit, categories, category_path)
    product = kit.create_product(category_id)
    product_id = s(product.get("id"))
    if not product_id:
        raise RuntimeError("KIT did not return product id")

    safe_part = re.sub(r"[^0-9A-Za-z]+", "-", item["article"])[:45].strip("-") or "ITEM"
    suffix = hashlib.sha1(item["article"].encode("utf-8")).hexdigest()[:10]
    temp_sku = f"NORDEN-TMP-{safe_part}-{suffix}"

    # Create with the minimal payload proven to work in KIT.
    # Stable site code and final Article are patched only after KIT returns kit_id.
    body = {
        "sku": temp_sku,
        "name": item["name"],
        "status": "PUBLISHED",
        "product_id": product_id,
        "brand": BRAND,
    }
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

    final_chars = [
        {
            "characteristic_id": code_site_id,
            "value": item["article"],
            "values": [item["article"]],
        },
        {
            "characteristic_id": article_id,
            "value": final_article,
            "values": [final_article],
        },
    ]
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
    ap.add_argument("--no-create", action="store_true", help="Update/reconcile existing Norden cards only; never create new KIT products")
    ap.add_argument("--time-budget-seconds", type=int, default=17000)
    args = ap.parse_args()

    report = {
        "started_at": now_iso(),
        "mode": args.mode,
        "source": None,
        "catalog_source": "API Norden; fallback Norden.xml",
        "price_stock_source": "API Norden; fallback Norden.group -K8%.xml",
        "api_error": None,
        "source_products": 0,
        "source_duplicate_articles": 0,
        "mapped_existing_articles": 0,
        "planned_new_products": 0,
        "new_products_created": 0,
        "new_product_creation_enabled": not args.no_create,
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

    # User rule: Norden items explicitly marked as available only in Moscow
    # must not be published or recreated in KIT.
    excluded_moscow_only = []
    excluded_without_images = []
    excluded_without_purchase_price = []
    if not short:
        excluded_moscow_only = [
            article for article, item in source.items()
            if is_moscow_only_product(item)
        ]
        # "Only Moscow" products are excluded from the managed KIT catalog entirely.
        for article in excluded_moscow_only:
            source.pop(article, None)

        # Missing image / missing purchase price blocks creation of NEW cards only.
        # Existing Norden cards remain in source so their valid stock/price fields
        # can still be refreshed on later runs.
        excluded_without_images = [
            article for article, item in source.items()
            if not (item.get("images") or [])
        ]
        excluded_without_purchase_price = [
            article for article, item in source.items()
            if dec(item.get("purchase")) is None or dec(item.get("purchase")) <= 0
        ]
    report["excluded_moscow_only_products"] = len(excluded_moscow_only)
    report["excluded_moscow_only_articles"] = excluded_moscow_only[:200]
    report["new_creation_blocked_without_images"] = len(excluded_without_images)
    report["new_creation_blocked_without_images_articles"] = excluded_without_images[:200]
    report["new_creation_blocked_without_purchase_price"] = len(excluded_without_purchase_price)
    report["new_creation_blocked_without_purchase_price_articles"] = excluded_without_purchase_price[:200]

    report["source_products"] = len(source)
    report["source_duplicate_articles"] = len(set(duplicates))

    if len(source) < (100 if short else 1000):
        raise RuntimeError(f"Safety stop: unexpectedly small Norden source ({len(source)} products)")

    all_chars, chars_by_title, code_site_id, article_id = resolve_special_characteristics(kit)
    identity_char_ids = resolve_identity_characteristic_ids(all_chars)
    report["identity_fields_checked"] = {
        title: ids for title, ids in identity_char_ids.items()
    }
    report["name_matching_policy"] = "NO"
    mapping = load_mapping()

    if args.rebuild_map or not mapping.get("variants"):
        if short:
            # Need the full article list for safe mapping.
            full_source, _, _, _ = load_source(secret, short=False)
            mapping = seed_mapping_from_existing_codes(full_source, report) if args.mode in ("preflight", "full") else None
            if mapping is None:
                mapping = rebuild_mapping_from_sku_list(kit, full_source, identity_char_ids, report)
            if mapping is None:
                mapping = rebuild_mapping(kit, full_source, identity_char_ids, report)
        else:
            mapping = seed_mapping_from_existing_codes(source, report) if args.mode in ("preflight", "full") else None
            if mapping is None:
                mapping = rebuild_mapping_from_sku_list(kit, source, identity_char_ids, report)
            if mapping is None:
                mapping = rebuild_mapping(kit, source, identity_char_ids, report)
    else:
        report["mapped_existing_articles"] = len(mapping.get("variants", {}))

    # Recover cards created by interrupted/older runs before deciding what is missing.
    # This prevents duplicates and gives us a list of bare cards to repair.
    repair_recent = []
    if args.mode in ("full", "scheduled") and not short:
        repair_recent = merge_recent_norden_mapping(
            kit, source, code_site_id, mapping, report, pages=10
        )

        # HARD DUPLICATE GUARD.
        # Before creating even one new Norden card, rescan the ENTIRE active Norden
        # catalog in KIT and rebuild mapping from all hard identifiers:
        # SKU / Артикул / Код продавца / Код Norden / External ID / Код для сайта.
        # Product name is explicitly disabled for Norden.
        tentative_missing = [a for a in source if a not in mapping.get("variants", {})]
        if tentative_missing:
            report["precreation_full_reconciliation_requested"] = len(tentative_missing)
            mapping = rebuild_mapping(kit, source, identity_char_ids, report)
            report["precreation_full_reconciliation_done"] = True
            duplicate_articles = {
                article: rows for article, rows in mapping.get("variants", {}).items()
                if len({s(x.get("variant_id")) for x in (rows or []) if s(x.get("variant_id"))}) > 1
            }
            report["existing_duplicate_norden_articles"] = len(duplicate_articles)
            report["existing_duplicate_norden_articles_sample"] = {
                article: rows[:10] for article, rows in list(duplicate_articles.items())[:100]
            }

            # Keep only one canonical card per supplier article. Legacy AF-* cards
            # win over newly auto-created 100-* cards. All non-canonical duplicates
            # are forced to zero stock and excluded from further price/stock updates.
            canonicalize_duplicate_mapping(kit, mapping, warehouses, report)
            save_mapping(mapping)

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

    # Repair cards left bare by earlier interrupted runs before creating more.
    if args.mode in ("full", "scheduled") and repair_recent:
        repaired_seen = set()
        for article, vid in repair_recent:
            key = (article, vid)
            if key in repaired_seen:
                continue
            repaired_seen.add(key)
            item = source.get(article)
            if not item:
                continue
            try:
                fill_existing_content(
                    kit, item, vid,
                    all_chars, chars_by_title, code_site_id, report
                )
                report.setdefault("bare_cards_repaired", 0)
                report["bare_cards_repaired"] += 1
                time.sleep(1.5)
            except Exception as exc:
                report["errors"].append({
                    "article": article,
                    "stage": "repair_bare",
                    "message": str(exc)[:800],
                })

    # Preflight performs no writes other than local report/mapping files.
    creation_blocked = set(excluded_without_images) | set(excluded_without_purchase_price)
    missing_all = [a for a in source if a not in mapping.get("variants", {})]
    missing = [a for a in missing_all if a not in creation_blocked]
    report["planned_new_products"] = len(missing)
    report["new_products_blocked_by_required_fields"] = len(missing_all) - len(missing)
    report["new_products_suppressed_by_no_create"] = len(missing) if args.no_create else 0
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

    # During duplicate-cleanup windows creation can be hard-disabled.
    if args.no_create:
        report["warnings"].append(
            "Создание новых Norden временно отключено (--no-create); обновлены только существующие карточки."
        )
        remaining_all = [a for a in source if a not in mapping.get("variants", {})]
        remaining = [a for a in remaining_all if a not in creation_blocked]
        report["remaining_new_products"] = len(remaining)
        report["remaining_blocked_by_required_fields"] = len(remaining_all) - len(remaining)
        report["initial_complete"] = bool(mapping.get("initial_complete"))
        report["finished_at"] = now_iso()
        save_mapping(mapping)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    # Create missing Norden products first so new cards appear in KIT immediately.
    categories = kit.categories()
    created_articles = []
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
        approved_path = approved_category_path(item.get("category_path"))
        if not approved_path:
            report.setdefault("category_review_required", []).append({
                "article": article,
                "source_category_path": " > ".join(item.get("category_path") or []),
                "reason": "NO_APPROVED_HIGH_CONFIDENCE_CATEGORY_RULE",
            })
            continue
        item = dict(item)
        item["category_path"] = approved_path
        try:
            new = create_new_product(
                kit, item, categories, all_chars, chars_by_title, code_site_id, article_id, warehouses, report
            )
            mapping["variants"][article] = [new]
            save_mapping(mapping)
            created_articles.append(article)
            report["new_products_created"] += 1

            # Price and stocks are already included in POST /v1/variants.
            ps = price_set(item.get("purchase"))
            if ps:
                report["price_updates"] += 1
            if item.get("stock") is not None:
                report["stock_updates"] += len(warehouses)

            # Enrich this card immediately: description, characteristics and images.
            # Do not wait until the whole batch is created.
            fill_existing_content(
                kit, item, new["variant_id"],
                all_chars, chars_by_title, code_site_id, report
            )
            time.sleep(1.5)
        except Exception as exc:
            report["errors"].append({"article": article, "stage": "create", "message": str(exc)[:800]})
            if len(report["errors"]) >= 200:
                report["warnings"].append("Error limit reached; stopping product creation.")
                break

    remaining_all = [a for a in source if a not in mapping.get("variants", {})]
    remaining = [a for a in remaining_all if a not in creation_blocked]
    mapping["initial_complete"] = len(remaining) == 0
    report["remaining_new_products"] = len(remaining)
    report["remaining_blocked_by_required_fields"] = len(remaining_all) - len(remaining)
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
