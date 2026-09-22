#!/usr/bin/env python3
import base64
import gzip
import hashlib
import io
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
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


def now_iso():
    return datetime.now(timezone.utc).isoformat()


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


def chunk_text(text: str, size: int = 40000, parts: int = 5):
    text = text or ""
    out = [text[i * size:(i + 1) * size] for i in range(parts)]
    if len(text) > size * parts:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        out[-1] += f"[TRUNCATED sha256={digest}]"
    return out


class HttpError(RuntimeError):
    def __init__(self, status, url, body):
        super().__init__(f"HTTP {status} {url}: {body[:500]}")
        self.status = status
        self.url = url
        self.body = body


class KitClient:
    def __init__(self, token: str):
        token = (token or "").strip()
        if not token:
            raise RuntimeError("YANDEX_KIT_TOKEN is empty")
        self.session = requests.Session()
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "megapolis-kit-backup/1.0",
        }
        self.last_request = 0.0
        self.min_interval = 0.42

    def request(self, method: str, path: str, *, params=None, timeout=120):
        url = KIT_BASE + path
        for attempt in range(12):
            delay = self.min_interval - (time.monotonic() - self.last_request)
            if delay > 0:
                time.sleep(delay)
            self.last_request = time.monotonic()
            r = self.session.request(method, url, params=params, headers=self.headers, timeout=timeout)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After") or min(45, 3 * (attempt + 1))))
                continue
            if r.status_code >= 500:
                time.sleep(min(30, 2 ** attempt))
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

    def collection(self, path: str):
        page = 1
        out = []
        while True:
            payload = self.request("GET", path, params={"page": page, "per_page": 100})
            rows = [x for x in self.items(payload) if isinstance(x, dict)]
            out.extend(rows)
            total = self.total(payload)
            print(f"{path}: page={page}, received={len(rows)}, total_collected={len(out)}, total={total}", flush=True)
            if not rows or (total is not None and len(out) >= total) or (total is None and len(rows) < 100):
                break
            page += 1
        return out


def try_collection(client: KitClient, path: str, warnings: list[str]):
    try:
        return client.collection(path)
    except Exception as exc:
        warnings.append(f"{path}: {exc}")
        return []


def load_from_api(token: str):
    warnings = []
    client = KitClient(token)
    variants = client.collection("/v1/variants")
    if not variants:
        raise RuntimeError("KIT API returned zero variants")
    characteristics = try_collection(client, "/v1/characteristics", warnings)
    categories = try_collection(client, "/v1/categories", warnings)
    warehouses = try_collection(client, "/v1/warehouses", warnings)
    products = try_collection(client, "/v1/products", warnings)
    return {
        "source": "KIT_API",
        "variants": variants,
        "characteristics": characteristics,
        "categories": categories,
        "warehouses": warehouses,
        "products": products,
        "warnings": warnings,
    }


def local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def load_from_yml(url: str):
    r = requests.get(url, timeout=240, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    payload = r.content
    variants = []
    categories = []
    for event, elem in ET.iterparse(io.BytesIO(payload), events=("end",)):
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
        "variants": variants,
        "characteristics": [],
        "categories": categories,
        "warehouses": [],
        "products": [],
        "warnings": ["KIT API unavailable; backup created from the public YML fallback."],
        "yml_bytes": len(payload),
    }


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

    product_headers = [
        "variant_id", "kit_id", "sku", "name", "brand", "barcode", "status",
        "product_id", "product_card_id", "description", "seo_description", "seo_h1", "seo_title",
        "slug", "relative_link_url", "vat", "requires_marking", "created_at", "updated_at",
        "price", "manual_discount_price", "promotion_price", "final_price",
        "stocks_count", "media_count", "characteristics_count",
        "cargo_boxes_json", "pricing_json", "stocks_json", "media_json", "characteristics_json",
        "raw_json_1", "raw_json_2", "raw_json_3", "raw_json_4", "raw_json_5",
    ]
    product_rows = []
    characteristic_rows = []
    stock_rows = []
    media_rows = []

    for v in data.get("variants") or []:
        pricing = v.get("pricing") if isinstance(v.get("pricing"), dict) else {}
        raw_parts = chunk_text(json_text(v))
        product_rows.append([
            clean(v.get("id")), clean(v.get("kit_id")), clean(v.get("sku")), clean(v.get("name")),
            clean(v.get("brand")), clean(v.get("barcode")), clean(v.get("status")),
            clean(v.get("product_id")), clean(v.get("product_card_id")), clean(v.get("description")),
            clean(v.get("seo_description")), clean(v.get("seo_h1")), clean(v.get("seo_title")),
            clean(v.get("slug")), clean(v.get("relative_link_url")), clean(v.get("vat")),
            clean(v.get("requires_marking")), clean(v.get("created_at")), clean(v.get("updated_at")),
            clean(pricing.get("price")), clean(pricing.get("manual_discount_price")),
            clean(pricing.get("promotion_price")), clean(pricing.get("final_price")),
            len(v.get("stocks") or []), len(v.get("media") or []), len(v.get("characteristics") or []),
            json_text(v.get("cargo_boxes") or []), json_text(pricing),
            json_text(v.get("stocks") or []), json_text(v.get("media") or []),
            json_text(v.get("characteristics") or []),
            *raw_parts,
        ])
        for c in v.get("characteristics") or []:
            cid = clean(c.get("characteristic_id") or c.get("id") or c.get("title"))
            characteristic_rows.append([
                clean(v.get("id")), clean(v.get("kit_id")), clean(v.get("sku")),
                cid, clean(c.get("title") or chars_by_id.get(cid)), clean(c.get("unit")),
                clean(c.get("value")), json_text(c.get("values") or []), json_text(c),
            ])
        for st in v.get("stocks") or []:
            wid = clean(st.get("warehouse_id"))
            stock_rows.append([
                clean(v.get("id")), clean(v.get("kit_id")), clean(v.get("sku")),
                wid, wh_by_id.get(wid, ""), clean(st.get("quantity")), clean(st.get("reserved")),
                clean(st.get("available_quantity")), json_text(st),
            ])
        for m in v.get("media") or []:
            media_rows.append([
                clean(v.get("id")), clean(v.get("kit_id")), clean(v.get("sku")),
                clean(m.get("image_id")), clean(m.get("url")), clean(m.get("type")),
                clean(m.get("display_sequence")), json_text(m),
            ])

    characteristic_dict_headers = ["id", "title", "type", "select_mode", "status", "raw_json"]
    characteristic_dict_rows = [[
        clean(x.get("id")), clean(x.get("title")), clean(x.get("type")),
        clean(x.get("select_mode")), clean(x.get("status")), json_text(x)
    ] for x in data.get("characteristics") or []]

    category_headers = ["id", "title", "parent_id", "status", "raw_json"]
    category_rows = [[
        clean(x.get("id")), clean(x.get("title") or x.get("name")),
        clean(x.get("parent_id")), clean(x.get("status")), json_text(x)
    ] for x in data.get("categories") or []]

    warehouse_headers = ["id", "title", "status", "raw_json"]
    warehouse_rows = [[
        clean(x.get("id")), clean(x.get("title") or x.get("name")), clean(x.get("status")), json_text(x)
    ] for x in data.get("warehouses") or []]

    api_product_headers = ["id", "category_ids_json", "created_at", "updated_at", "raw_json_1", "raw_json_2", "raw_json_3"]
    api_product_rows = []
    for x in data.get("products") or []:
        chunks = chunk_text(json_text(x), parts=3)
        api_product_rows.append([
            clean(x.get("id")), json_text(x.get("category_ids") or []),
            clean(x.get("created_at")), clean(x.get("updated_at")), *chunks[:3]
        ])

    return {
        "Товары": (product_headers, product_rows),
        "Характеристики": (["variant_id", "kit_id", "sku", "characteristic_id", "characteristic_title", "unit", "value", "values_json", "raw_json"], characteristic_rows),
        "Остатки": (["variant_id", "kit_id", "sku", "warehouse_id", "warehouse_title", "quantity", "reserved", "available_quantity", "raw_json"], stock_rows),
        "Медиа": (["variant_id", "kit_id", "sku", "image_id", "url", "type", "display_sequence", "raw_json"], media_rows),
        "Справочник характеристик": (characteristic_dict_headers, characteristic_dict_rows),
        "Категории": (category_headers, category_rows),
        "Склады": (warehouse_headers, warehouse_rows),
        "Продукты API": (api_product_headers, api_product_rows),
    }


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


def sync_google(tables, report, sheet_id: str, credential_raw: str):
    import gspread

    info = service_account_info(credential_raw)
    if not info:
        return False, "GOOGLE_SERVICE_ACCOUNT_JSON is not configured"

    gc = gspread.service_account_from_dict(info)
    sh = gc.open_by_key(sheet_id)

    total_cells = 0

    def write_partitioned(base_title, headers, rows, max_data_rows=100000):
        nonlocal total_cells
        if not headers:
            return
        parts = max(1, (len(rows) + max_data_rows - 1) // max_data_rows)
        for idx in range(parts):
            title = base_title if idx == 0 else f"{base_title}_{idx + 1}"
            subset = rows[idx * max_data_rows:(idx + 1) * max_data_rows]
            rcount = max(2, len(subset) + 1)
            ccount = max(1, len(headers))
            total_cells += rcount * ccount
            try:
                ws = sh.worksheet(title)
                ws.clear()
                ws.resize(rows=rcount, cols=ccount)
            except gspread.WorksheetNotFound:
                ws = sh.add_worksheet(title=title, rows=rcount, cols=ccount)
            ws.update(range_name="A1", values=[headers], value_input_option="RAW")
            for start in range(0, len(subset), 2000):
                block = subset[start:start + 2000]
                row_start = start + 2
                ws.update(range_name=f"A{row_start}", values=block, value_input_option="RAW")
            try:
                ws.freeze(rows=1)
            except Exception:
                pass

        for ws in list(sh.worksheets()):
            t = ws.title
            if t.startswith(base_title + "_"):
                suffix = t[len(base_title) + 1:]
                if suffix.isdigit() and int(suffix) > parts:
                    sh.del_worksheet(ws)

    for title, (headers, rows) in tables.items():
        if title == "Характеристики":
            write_partitioned(title, headers, rows, max_data_rows=80000)
        else:
            write_partitioned(title, headers, rows, max_data_rows=100000)

    if total_cells > 9_500_000:
        raise RuntimeError(f"Estimated sheet size is too large: {total_cells} cells")

    meta = [
        ["Параметр", "Значение"],
        ["Последняя синхронизация UTC", report["finished_at"]],
        ["Источник", report["source"]],
        ["Товаров / вариантов", report["counts"]["variants"]],
        ["Характеристик (значений)", report["counts"]["characteristic_values"]],
        ["Остатков (строк)", report["counts"]["stocks"]],
        ["Медиа (строк)", report["counts"]["media"]],
        ["Категорий", report["counts"]["categories"]],
        ["Складов", report["counts"]["warehouses"]],
        ["Справочник характеристик", report["counts"]["characteristic_definitions"]],
        ["Продуктов API", report["counts"]["products"]],
        ["YML fallback", report["fallback_url"]],
        ["Предупреждения", " | ".join(report.get("warnings") or [])],
    ]
    try:
        ws = sh.worksheet("Сводка")
        ws.clear()
        ws.resize(rows=max(20, len(meta) + 2), cols=2)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="Сводка", rows=max(20, len(meta) + 2), cols=2)
    ws.update(range_name="A1", values=meta, value_input_option="RAW")
    try:
        history = sh.worksheet("История")
    except gspread.WorksheetNotFound:
        history = sh.add_worksheet(title="История", rows=1000, cols=8)
        history.append_row(["timestamp_utc", "source", "variants", "characteristic_values", "stocks", "media", "categories", "warnings"], value_input_option="RAW")
    history.append_row([
        report["finished_at"], report["source"], report["counts"]["variants"],
        report["counts"]["characteristic_values"], report["counts"]["stocks"],
        report["counts"]["media"], report["counts"]["categories"],
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

    tables = build_tables(data)
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
        "fallback_url": fallback_url,
        "google_sheet_id": sheet_id,
        "api_error": api_error,
        "warnings": data.get("warnings") or [],
        "counts": counts,
        "google_synced": False,
        "google_message": "",
    }

    backup_path = save_full_backup(data, report)
    report["backup_file"] = str(backup_path.relative_to(ROOT))
    report["backup_bytes"] = backup_path.stat().st_size

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
    report_path = RUNTIME / "last_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)

    if counts["variants"] == 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
