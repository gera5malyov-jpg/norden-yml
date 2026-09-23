#!/usr/bin/env python3
import base64
import gzip
import hashlib
import io
import json
import math
import os
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

KIT_BASE = "https://api.kit.yandex.net"
ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
RUNTIME.mkdir(parents=True, exist_ok=True)

DEFAULT_SHEET_ID = "1xZUeaWUr-6O3KK0MO-1U7IGBKXXfgcmZ-EJ-w-2wWug"
DEFAULT_FALLBACK_URL = "https://yastore-prod-persist.s3.yandex.net/feeds/yml/019a5a60-ce41-7872-aa9d-d7720c268dab.xml"
MAX_CELL_CHARS = 48000


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return str(value)


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def safe_cell(value: Any, limit: int = MAX_CELL_CHARS) -> str:
    text = clean(value)
    if len(text) <= limit:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    suffix = f"...[TRUNCATED sha256={digest}]"
    return text[: max(0, limit - len(suffix))] + suffix


def chunk_text(text: str, size: int = 40000, parts: int = 5):
    text = text or ""
    out = [text[i * size:(i + 1) * size] for i in range(parts)]
    if len(text) > size * parts:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        suffix = f"[TRUNCATED sha256={digest}]"
        out[-1] = (out[-1][: max(0, size - len(suffix))] + suffix)
    return out


class HttpError(RuntimeError):
    def __init__(self, status, url, body):
        super().__init__(f"HTTP {status} {url}: {body[:500]}")
        self.status = status
        self.url = url
        self.body = body


class KitClient:
    def __init__(self, token: str, workers: int = 6):
        token = (token or "").strip()
        if not token:
            raise RuntimeError("YANDEX_KIT_TOKEN is empty")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "megapolis-kit-backup/2.0",
        }
        self.workers = max(1, min(8, int(workers or 1)))

    def request(self, method: str, path: str, *, params=None, timeout=120):
        url = KIT_BASE + path
        session = requests.Session()
        for attempt in range(14):
            try:
                r = session.request(method, url, params=params, headers=self.headers, timeout=timeout)
            except requests.RequestException:
                time.sleep(min(20, 1 + attempt * 2))
                continue
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After") or min(45, 2 + attempt * 3))
                time.sleep(wait)
                continue
            if r.status_code >= 500:
                time.sleep(min(30, 2 ** min(attempt, 5)))
                continue
            if r.status_code >= 400:
                raise HttpError(r.status_code, r.url, r.text)
            return r.json() if r.content else {}
        raise RuntimeError(f"KIT retries exhausted: {method} {path}")

    @staticmethod
    def items(payload):
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        for key in ("items", "results", "variants", "warehouses", "categories", "characteristics", "products"):
            if isinstance(payload.get(key), list):
                return payload[key]
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

    def collection(self, path: str, params=None, parallel=True):
        q = dict(params or {})
        q.update({"page": 1, "per_page": 100})
        first = self.request("GET", path, params=q)
        first_rows = [x for x in self.items(first) if isinstance(x, dict)]
        total = self.total(first)
        print(f"{path}: page=1, received={len(first_rows)}, total={total}", flush=True)

        if not first_rows:
            return []
        if total is None:
            out = list(first_rows)
            page = 2
            while True:
                q2 = dict(params or {})
                q2.update({"page": page, "per_page": 100})
                payload = self.request("GET", path, params=q2)
                rows = [x for x in self.items(payload) if isinstance(x, dict)]
                out.extend(rows)
                if page % 25 == 0 or len(rows) < 100:
                    print(f"{path}: page={page}, total_collected={len(out)}", flush=True)
                if not rows or len(rows) < 100:
                    break
                page += 1
            return out

        pages = max(1, math.ceil(total / 100))
        if pages == 1:
            return first_rows

        if not parallel or self.workers == 1:
            out = list(first_rows)
            for page in range(2, pages + 1):
                q2 = dict(params or {})
                q2.update({"page": page, "per_page": 100})
                payload = self.request("GET", path, params=q2)
                out.extend([x for x in self.items(payload) if isinstance(x, dict)])
                if page % 25 == 0 or page == pages:
                    print(f"{path}: {page}/{pages} pages", flush=True)
            return out

        def fetch_page(page):
            q2 = dict(params or {})
            q2.update({"page": page, "per_page": 100})
            payload = self.request("GET", path, params=q2)
            return page, [x for x in self.items(payload) if isinstance(x, dict)]

        by_page = {1: first_rows}
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = [pool.submit(fetch_page, p) for p in range(2, pages + 1)]
            done = 1
            for fut in as_completed(futures):
                page, rows = fut.result()
                by_page[page] = rows
                done += 1
                if done % 25 == 0 or done == pages:
                    print(f"{path}: {done}/{pages} pages", flush=True)

        out = []
        for page in range(1, pages + 1):
            out.extend(by_page.get(page, []))
        return out


def try_collection(client: KitClient, path: str, warnings: list[str], params=None, parallel=True):
    try:
        return client.collection(path, params=params, parallel=parallel)
    except Exception as exc:
        warnings.append(f"{path}: {exc}")
        return []


def load_from_api(token: str):
    warnings = []
    workers = int(os.getenv("KIT_API_WORKERS", "6") or 6)
    client = KitClient(token, workers=workers)

    t0 = time.monotonic()
    variants = client.collection("/v1/variants", parallel=True)
    if not variants:
        raise RuntimeError("KIT API returned zero variants")

    characteristics = try_collection(
        client, "/v1/characteristics", warnings, params={"status": "ACTIVE"}, parallel=True
    )
    categories = try_collection(
        client, "/v1/categories", warnings, params={"status": "ACTIVE"}, parallel=True
    )
    warehouses = try_collection(
        client, "/v1/warehouses", warnings, params={"status": "ACTIVE"}, parallel=True
    )

    include_products = env_bool("KIT_INCLUDE_PRODUCTS", False)
    products = []
    if include_products:
        products = try_collection(client, "/v1/products", warnings, parallel=False)

    return {
        "source": "KIT_API",
        "mode": "DEEP" if include_products else "FAST",
        "variants": variants,
        "characteristics": characteristics,
        "categories": categories,
        "warehouses": warehouses,
        "products": products,
        "warnings": warnings,
        "api_seconds": round(time.monotonic() - t0, 2),
    }


def local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def load_from_yml(url: str):
    t0 = time.monotonic()
    r = requests.get(url, timeout=240, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    payload = r.content
    variants = []
    categories = []
    for _, elem in ET.iterparse(io.BytesIO(payload), events=("end",)):
        tag = local_tag(elem.tag)
        if tag == "category":
            categories.append({
                "id": elem.attrib.get("id", ""),
                "parent_id": elem.attrib.get("parentId", ""),
                "title": (elem.text or "").strip(),
                "raw_attributes": dict(elem.attrib),
            })
            elem.clear()
        elif tag == "offer":
            data = {
                "id": elem.attrib.get("id", ""),
                "kit_id": elem.attrib.get("id", ""),
                "sku": "",
                "name": "",
                "brand": "",
                "barcode": "",
                "status": "PUBLISHED" if elem.attrib.get("available", "true").lower() != "false" else "UNPUBLISHED",
                "description": "",
                "seo_description": "",
                "seo_h1": "",
                "seo_title": "",
                "slug": "",
                "relative_link_url": "",
                "requires_marking": "",
                "vat": "",
                "created_at": "",
                "updated_at": "",
                "pricing": {},
                "stocks": [],
                "media": [],
                "characteristics": [],
                "cargo_boxes": [],
                "product_id": "",
                "product_card_id": "",
                "_yml_attributes": dict(elem.attrib),
                "_yml_extra": {},
            }
            extras = {}
            for child in list(elem):
                ctag = local_tag(child.tag)
                txt = (child.text or "").strip()
                if ctag == "name":
                    data["name"] = txt
                elif ctag == "vendor":
                    data["brand"] = txt
                elif ctag in ("vendorCode", "sku"):
                    data["sku"] = txt
                elif ctag == "barcode":
                    data["barcode"] = txt
                elif ctag == "description":
                    data["description"] = txt
                elif ctag == "url":
                    data["relative_link_url"] = txt
                elif ctag == "price":
                    data["pricing"]["final_price"] = txt
                    data["pricing"]["manual_discount_price"] = txt
                elif ctag == "oldprice":
                    data["pricing"]["price"] = txt
                    data["pricing"]["promotion_price"] = txt
                elif ctag == "picture":
                    data["media"].append({
                        "url": txt,
                        "type": "IMAGE",
                        "display_sequence": len(data["media"]),
                    })
                elif ctag == "param":
                    data["characteristics"].append({
                        "characteristic_id": child.attrib.get("name", ""),
                        "title": child.attrib.get("name", ""),
                        "unit": child.attrib.get("unit", ""),
                        "value": txt,
                        "values": [txt] if txt else [],
                    })
                elif ctag in ("count", "quantity"):
                    data["stocks"].append({"quantity": txt, "warehouse_id": "", "reserved": ""})
                else:
                    if ctag in extras:
                        if not isinstance(extras[ctag], list):
                            extras[ctag] = [extras[ctag]]
                        extras[ctag].append({"value": txt, "attributes": dict(child.attrib)})
                    else:
                        extras[ctag] = {"value": txt, "attributes": dict(child.attrib)}
            data["_yml_extra"] = extras
            variants.append(data)
            elem.clear()
    return {
        "source": "YML_FALLBACK",
        "mode": "FAST",
        "variants": variants,
        "characteristics": [],
        "categories": categories,
        "warehouses": [],
        "products": [],
        "warnings": ["KIT API unavailable; backup created from the public YML fallback."],
        "yml_bytes": len(payload),
        "api_seconds": round(time.monotonic() - t0, 2),
    }


def _norm_title(value):
    return " ".join(str(value or "").replace("ё", "е").replace("Ё", "Е").casefold().split())


COMMON_CHARACTERISTICS = [
    ("Размер", ["размер"]),
    ("Вес, кг", ["вес, кг", "вес кг", "вес"]),
    ("Артикул", ["артикул"]),
    ("Артикул поставщика", ["артикул поставщика"]),
    ("Количество мест (упаковок)", ["количество мест (упаковок)", "количество мест", "кол-во мест", "количество упаковок"]),
    ("Цвет изделия", ["цвет изделия", "цвет"]),
    ("Код для сайта", ["код для сайта"]),
    ("Объем, м³", ["объем, м³", "объем м3", "объем, м3", "объем"]),
    ("Материал", ["материал"]),
    ("Материал каркаса", ["материал каркаса"]),
    ("Материал обивки", ["материал обивки"]),
    ("Страна производства", ["страна производства", "страна"]),
]


def build_tables(data):
    chars_by_id = {}
    for c in data.get("characteristics") or []:
        cid = clean(c.get("id"))
        if cid:
            chars_by_id[cid] = clean(c.get("title") or c.get("name"))

    wh_by_id = {}
    for w in data.get("warehouses") or []:
        wid = clean(w.get("id"))
        if wid:
            wh_by_id[wid] = clean(w.get("title") or w.get("name"))

    headers = [
        "ID варианта", "ID KIT", "Артикул KIT", "Название", "Бренд",
        "Поставщик", "Цена закупки", "Дата создания в KIT",
        "Штрихкод", "Статус", "ID товара", "ID карточки",
        *[x[0] for x in COMMON_CHARACTERISTICS],
        "Описание", "SEO-описание", "SEO H1", "SEO-заголовок",
        "Слаг", "Ссылка в KIT", "НДС", "Требует маркировки", "Дата создания (API)", "Дата обновления",
        "Цена до скидки", "Цена со скидкой вручную", "Промо-цена", "Итоговая цена", "Цены JSON",
        "Все характеристики JSON", "Остатки JSON", "Медиа JSON", "Упаковки JSON", "Доп. данные JSON",
    ]
    rows = []
    all_characteristic_rows = []

    flattened = {
        "id", "kit_id", "sku", "name", "brand", "barcode", "status",
        "product_id", "product_card_id", "description", "seo_description", "seo_h1", "seo_title",
        "slug", "relative_link_url", "vat", "requires_marking", "created_at", "updated_at",
        "pricing", "characteristics", "stocks", "media", "cargo_boxes",
    }

    for v in data.get("variants") or []:
        pricing = v.get("pricing") if isinstance(v.get("pricing"), dict) else {}
        sku = safe_cell(v.get("sku"))

        enriched_chars = []
        char_values = {}
        for c in v.get("characteristics") or []:
            item = dict(c) if isinstance(c, dict) else {"value": c}
            cid = clean(item.get("characteristic_id") or item.get("id") or item.get("title"))
            if not item.get("title") and chars_by_id.get(cid):
                item["title"] = chars_by_id[cid]

            title = clean(item.get("title") or chars_by_id.get(cid) or cid)
            values = []
            if item.get("value") not in (None, ""):
                values.append(clean(item.get("value")))
            for value in item.get("values") or []:
                value = clean(value)
                if value and value not in values:
                    values.append(value)
            display_value = " | ".join(values)

            norm = _norm_title(title)
            if display_value:
                all_characteristic_rows.append([
                    sku,
                    safe_cell(title),
                    safe_cell(display_value),
                ])

                for label, aliases in COMMON_CHARACTERISTICS:
                    if label in char_values:
                        continue
                    alias_norms = {_norm_title(x) for x in aliases}
                    if norm in alias_norms:
                        char_values[label] = display_value

            enriched_chars.append(item)

        enriched_stocks = []
        for st in v.get("stocks") or []:
            item = dict(st) if isinstance(st, dict) else {"value": st}
            wid = clean(item.get("warehouse_id"))
            if wid and wh_by_id.get(wid):
                item["warehouse_title"] = wh_by_id[wid]
            enriched_stocks.append(item)

        extra = {k: val for k, val in v.items() if k not in flattened}

        rows.append([
            safe_cell(v.get("id")), safe_cell(v.get("kit_id")), sku, safe_cell(v.get("name")),
            safe_cell(v.get("brand")),
            "", "", safe_cell(v.get("created_at")),
            safe_cell(v.get("barcode")), safe_cell(v.get("status")),
            safe_cell(v.get("product_id")), safe_cell(v.get("product_card_id")),
            *[safe_cell(char_values.get(label, "")) for label, _ in COMMON_CHARACTERISTICS],
            safe_cell(v.get("description")),
            safe_cell(v.get("seo_description")), safe_cell(v.get("seo_h1")), safe_cell(v.get("seo_title")),
            safe_cell(v.get("slug")), safe_cell(v.get("relative_link_url")), safe_cell(v.get("vat")),
            safe_cell(v.get("requires_marking")), safe_cell(v.get("created_at")), safe_cell(v.get("updated_at")),
            safe_cell(pricing.get("price")), safe_cell(pricing.get("manual_discount_price")),
            safe_cell(pricing.get("promotion_price")), safe_cell(pricing.get("final_price")),
            safe_cell(json_text(pricing)),
            safe_cell(json_text(enriched_chars)),
            safe_cell(json_text(enriched_stocks)),
            safe_cell(json_text(v.get("media") or [])),
            safe_cell(json_text(v.get("cargo_boxes") or [])),
            safe_cell(json_text(extra)),
        ])

    characteristic_headers = ["ID", "Название", "Тип", "Режим выбора", "Статус", "Исходные данные JSON"]
    characteristic_rows = [[
        safe_cell(x.get("id")), safe_cell(x.get("title")), safe_cell(x.get("type")),
        safe_cell(x.get("select_mode")), safe_cell(x.get("status")), safe_cell(json_text(x))
    ] for x in data.get("characteristics") or []]

    category_headers = ["ID", "Название", "ID родительской категории", "Статус", "Исходные данные JSON"]
    category_rows = [[
        safe_cell(x.get("id")), safe_cell(x.get("title") or x.get("name")),
        safe_cell(x.get("parent_id")), safe_cell(x.get("status")), safe_cell(json_text(x))
    ] for x in data.get("categories") or []]

    warehouse_headers = ["ID", "Название", "Статус", "Исходные данные JSON"]
    warehouse_rows = [[
        safe_cell(x.get("id")), safe_cell(x.get("title") or x.get("name")),
        safe_cell(x.get("status")), safe_cell(json_text(x))
    ] for x in data.get("warehouses") or []]

    tables = {
        "Товары": (headers, rows),
        "Все характеристики": (["Артикул KIT", "Характеристика", "Значение"], all_characteristic_rows),
        "Справочник характеристик": (characteristic_headers, characteristic_rows),
        "Категории": (category_headers, category_rows),
        "Склады": (warehouse_headers, warehouse_rows),
    }

    if data.get("products"):
        p_headers = ["ID", "Категории JSON", "Дата создания", "Дата обновления", "Исходные данные JSON 1", "Исходные данные JSON 2", "Исходные данные JSON 3"]
        p_rows = []
        for x in data.get("products") or []:
            chunks = chunk_text(json_text(x), parts=3)
            p_rows.append([
                safe_cell(x.get("id")), safe_cell(json_text(x.get("category_ids") or [])),
                safe_cell(x.get("created_at")), safe_cell(x.get("updated_at")),
                *[safe_cell(v) for v in chunks[:3]]
            ])
        tables["Продукты API"] = (p_headers, p_rows)

    return tables


def service_account_info(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        return json.loads(raw)
    try:
        return json.loads(base64.b64decode(raw).decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON must be raw JSON or base64 JSON") from exc


def iter_write_blocks(rows, max_rows=1200, max_chars=900_000):
    block = []
    chars = 0
    for row in rows:
        row_chars = sum(len(str(v)) for v in row)
        if block and (len(block) >= max_rows or chars + row_chars > max_chars):
            yield block, chars
            block = []
            chars = 0
        block.append(row)
        chars += row_chars
    if block:
        yield block, chars


def sync_google(tables, report, sheet_id: str, credential_raw: str):
    import gspread

    info = service_account_info(credential_raw)
    if not info:
        return False, "GOOGLE_SERVICE_ACCOUNT_JSON is not configured"

    gc = gspread.service_account_from_dict(info)
    sh = gc.open_by_key(sheet_id)

    progress = [
        ["Параметр", "Значение"],
        ["Статус", "СИНХРОНИЗАЦИЯ ВЫПОЛНЯЕТСЯ"],
        ["Запуск UTC", report["started_at"]],
        ["Источник", report["source"]],
        ["Режим", report["mode"]],
        ["Товаров / вариантов получено", report["counts"]["variants"]],
    ]
    try:
        ws = sh.worksheet("Сводка")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="Сводка", rows=30, cols=2)
    ws.clear()
    ws.resize(rows=30, cols=2)
    ws.update(range_name="A1", values=progress, value_input_option="RAW")

    estimated_cells = 0
    for _, (headers, rows) in tables.items():
        estimated_cells += max(2, len(rows) + 1) * max(1, len(headers))
    if estimated_cells > 9_500_000:
        raise RuntimeError(f"Estimated Google Sheets size is too large: {estimated_cells} cells")

    def flush_batch(pending, title, written, total):
        if not pending:
            return
        sh.values_batch_update({
            "valueInputOption": "RAW",
            "data": pending,
        })
        print(f"Google Sheets {title}: {written}/{total} rows", flush=True)

    def write_table(title, headers, rows):
        try:
            sheet = sh.worksheet(title)
            sheet.clear()
            sheet.resize(rows=max(2, len(rows) + 1), cols=max(1, len(headers)))
        except gspread.WorksheetNotFound:
            sheet = sh.add_worksheet(
                title=title,
                rows=max(2, len(rows) + 1),
                cols=max(1, len(headers)),
            )

        a1_title = title.replace("'", "''")
        sheet.update(range_name="A1", values=[headers], value_input_option="RAW")

        pending = []
        pending_chars = 0
        row_start = 2
        written = 0

        for block, block_chars in iter_write_blocks(rows):
            pending.append({
                "range": f"'{a1_title}'!A{row_start}",
                "majorDimension": "ROWS",
                "values": block,
            })
            row_start += len(block)
            written += len(block)
            pending_chars += block_chars

            if pending_chars >= 5_500_000 or len(pending) >= 10:
                flush_batch(pending, title, written, len(rows))
                pending = []
                pending_chars = 0

        flush_batch(pending, title, written, len(rows))
        try:
            sheet.freeze(rows=1)
        except Exception:
            pass

    for title, (headers, rows) in tables.items():
        write_table(title, headers, rows)

    for obsolete in ("Sheet1", "Характеристики", "Остатки", "Медиа", "Продукты API"):
        if obsolete in tables:
            continue
        try:
            old = sh.worksheet(obsolete)
            sh.del_worksheet(old)
        except gspread.WorksheetNotFound:
            pass

    meta = [
        ["Параметр", "Значение"],
        ["Статус", "УСПЕШНО"],
        ["Последняя синхронизация UTC", now_iso()],
        ["Источник", report["source"]],
        ["Режим", report["mode"]],
        ["Товаров / вариантов", report["counts"]["variants"]],
        ["Характеристик (значений)", report["counts"]["characteristic_values"]],
        ["Остатков (записей)", report["counts"]["stocks"]],
        ["Медиа (записей)", report["counts"]["media"]],
        ["Категорий", report["counts"]["categories"]],
        ["Складов", report["counts"]["warehouses"]],
        ["Справочник характеристик", report["counts"]["characteristic_definitions"]],
        ["Продуктов API (глубокий режим)", report["counts"]["products"]],
        ["Время чтения API, сек.", report.get("api_seconds", "")],
        ["YML fallback", report["fallback_url"]],
        ["Предупреждения", " | ".join(report.get("warnings") or [])],
    ]
    ws.clear()
    ws.resize(rows=max(30, len(meta) + 2), cols=2)
    ws.update(range_name="A1", values=meta, value_input_option="RAW")

    try:
        history = sh.worksheet("История")
    except gspread.WorksheetNotFound:
        history = sh.add_worksheet(title="История", rows=1000, cols=10)
        history.append_row(
            ["Время UTC", "Источник", "Режим", "Товаров", "Значений характеристик", "Остатков", "Медиа", "Категорий", "Время API, сек.", "Предупреждения"],
            value_input_option="RAW",
        )
    history.append_row([
        now_iso(), report["source"], report["mode"], report["counts"]["variants"],
        report["counts"]["characteristic_values"], report["counts"]["stocks"], report["counts"]["media"],
        report["counts"]["categories"], report.get("api_seconds", ""),
        " | ".join(report.get("warnings") or []),
    ], value_input_option="RAW")

    return True, f"Google Sheet updated: {sh.url}"


def save_full_backup(data, report):
    path = RUNTIME / "kit_full_backup.json.gz"
    payload = {
        "meta": report,
        "variants": data.get("variants") or [],
        "characteristics": data.get("characteristics") or [],
        "categories": data.get("categories") or [],
        "warehouses": data.get("warehouses") or [],
        "products": data.get("products") or [],
    }
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    return path


def main():
    started_monotonic = time.monotonic()
    started = now_iso()
    token = os.getenv("YANDEX_KIT_TOKEN", "").strip()
    fallback_url = os.getenv("YML_FALLBACK_URL", DEFAULT_FALLBACK_URL).strip()
    sheet_id = os.getenv("GOOGLE_SHEET_ID", DEFAULT_SHEET_ID).strip()

    api_error = None
    try:
        if not token:
            raise RuntimeError("YANDEX_KIT_TOKEN is not configured")
        data = load_from_api(token)
    except Exception as exc:
        api_error = str(exc)
        print(f"KIT API failed; switching to YML fallback: {api_error}", file=sys.stderr, flush=True)
        data = load_from_yml(fallback_url)

    counts = {
        "variants": len(data.get("variants") or []),
        "characteristic_values": sum(len(x.get("characteristics") or []) for x in data.get("variants") or []),
        "stocks": sum(len(x.get("stocks") or []) for x in data.get("variants") or []),
        "media": sum(len(x.get("media") or []) for x in data.get("variants") or []),
        "characteristic_definitions": len(data.get("characteristics") or []),
        "categories": len(data.get("categories") or []),
        "warehouses": len(data.get("warehouses") or []),
        "products": len(data.get("products") or []),
    }

    report = {
        "started_at": started,
        "finished_at": now_iso(),
        "source": data.get("source"),
        "mode": data.get("mode", "FAST"),
        "fallback_url": fallback_url,
        "google_sheet_id": sheet_id,
        "api_error": api_error,
        "warnings": data.get("warnings") or [],
        "counts": counts,
        "api_seconds": data.get("api_seconds"),
        "google_synced": False,
        "google_message": "",
    }

    backup_path = save_full_backup(data, report)
    report["backup_file"] = str(backup_path.relative_to(ROOT))
    report["backup_bytes"] = backup_path.stat().st_size

    tables = build_tables(data)
    credential_raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    try:
        synced, message = sync_google(tables, report, sheet_id, credential_raw)
        report["google_synced"] = synced
        report["google_message"] = message
        if not synced:
            report["warnings"].append(message)
    except Exception as exc:
        report["google_message"] = f"Google Sheets sync failed: {exc}"
        report["warnings"].append(report["google_message"])

    report["finished_at"] = now_iso()
    report["total_seconds"] = round(time.monotonic() - started_monotonic, 2)
    report_path = RUNTIME / "last_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if counts["variants"] > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
