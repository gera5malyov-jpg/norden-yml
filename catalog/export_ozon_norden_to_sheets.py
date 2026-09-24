#!/usr/bin/env python3
import json, os, time, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

OZON_BASE = "https://api-seller.ozon.ru"
CLIENT_ID = os.environ["OZON_CLIENT_ID"].strip()
API_KEY = os.environ["OZON_API_KEY"].strip()
SA_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
OWNER_EMAIL = os.environ.get("CATALOG_OWNER_EMAIL", "gera5malyov@gmail.com").strip()
TITLE = os.environ.get("CATALOG_TITLE", "Каталог").strip()
SHEET = os.environ.get("CATALOG_SHEET", "Норден").strip()
SPREADSHEET_ID = os.environ.get("CATALOG_SPREADSHEET_ID", "").strip()
REPORT = Path("catalog/last_run.json")
BRAND_ATTR_ID = 85

HEADERS = {
    "Client-Id": CLIENT_ID,
    "Api-Key": API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "megapolis-ozon-catalog-export/1.0",
}

def post(path, payload, attempts=6):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(attempts):
        req = urllib.request.Request(OZON_BASE + path, data=body, headers=HEADERS, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            txt = exc.read().decode("utf-8", "replace")
            if (exc.code == 429 or 500 <= exc.code < 600) and attempt + 1 < attempts:
                time.sleep(min(2 ** attempt, 30))
                continue
            raise RuntimeError(f"{path}: HTTP {exc.code}: {txt[:1500]}")
        except Exception:
            if attempt + 1 < attempts:
                time.sleep(min(2 ** attempt, 30))
                continue
            raise

def chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i+n]

def list_visibility(visibility):
    out, last_id, seen = [], "", set()
    while True:
        payload = {"filter": {"visibility": visibility}, "limit": 1000}
        if last_id:
            payload["last_id"] = last_id
        d = post("/v3/product/list", payload)
        r = d.get("result") or {}
        items = r.get("items") or []
        out.extend(items)
        nxt = r.get("last_id") or ""
        total = int(r.get("total") or 0)
        if not items or len(out) >= total or not nxt or nxt == last_id or nxt in seen:
            break
        seen.add(last_id)
        last_id = nxt
    return out

def get_attributes(product_ids):
    out = []
    for batch in chunks(product_ids, 1000):
        d = post("/v4/product/info/attributes", {
            "filter": {"product_id": batch, "visibility": "ALL"},
            "limit": 1000,
        })
        out.extend(d.get("result") or [])
    return out

def get_info(product_ids):
    out = []
    errors = []
    for batch in chunks(product_ids, 1000):
        try:
            d = post("/v3/product/info/list", {"product_id": batch})
            out.extend((d.get("items") or (d.get("result") or {}).get("items") or []))
        except Exception as e:
            errors.append(str(e))
    return out, errors

def get_prices(product_ids):
    out, errors = [], []
    for batch in chunks(product_ids, 100):
        cursor = ""
        while True:
            try:
                d = post("/v5/product/info/prices", {
                    "cursor": cursor,
                    "filter": {"product_id": batch, "visibility": "ALL"},
                    "limit": 100,
                })
            except Exception as e:
                errors.append(str(e))
                break
            items = d.get("items") or []
            out.extend(items)
            nxt = d.get("cursor") or ""
            if not items or not nxt or nxt == cursor:
                break
            cursor = nxt
    return out, errors

def get_stocks(product_ids):
    out, errors = [], []
    for batch in chunks(product_ids, 100):
        cursor = ""
        while True:
            payload = {
                "filter": {"product_id": batch, "visibility": "ALL"},
                "limit": 100,
            }
            if cursor:
                payload["cursor"] = cursor
            try:
                d = post("/v4/product/info/stocks", payload)
            except Exception as e:
                errors.append(str(e))
                break
            items = d.get("items") or []
            out.extend(items)
            nxt = d.get("cursor") or ""
            if not items or not nxt or nxt == cursor:
                break
            cursor = nxt
    return out, errors

def attr_values(product, attr_id):
    for a in product.get("attributes") or []:
        if int(a.get("id") or a.get("attribute_id") or 0) == int(attr_id):
            return [str(v.get("value") or "").strip() for v in (a.get("values") or [])]
    return []

def is_norden(product):
    return any(v.upper() == "NORDEN" for v in attr_values(product, BRAND_ATTR_ID))

def j(x):
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))

def split_json(obj, chunk=45000, max_parts=16):
    s = j(obj)
    parts = [s[i:i+chunk] for i in range(0, len(s), chunk)]
    if len(parts) > max_parts:
        raise RuntimeError(f"JSON товара превышает вместимость {max_parts} ячеек")
    return parts + [""] * (max_parts - len(parts))

def first_brand(product):
    vals = attr_values(product, BRAND_ATTR_ID)
    return " | ".join(vals)

def main():
    active = list_visibility("ALL")
    archived = list_visibility("ARCHIVED")
    archived_ids = {int(x.get("product_id") or 0) for x in archived if int(x.get("product_id") or 0)}
    by_id = {}
    for x in active + archived:
        pid = int(x.get("product_id") or 0)
        if pid:
            by_id[pid] = x

    all_ids = sorted(by_id)
    attrs = get_attributes(all_ids)
    attr_by_id = {int(x.get("id") or x.get("product_id") or 0): x for x in attrs}
    norden_ids = sorted(pid for pid, p in attr_by_id.items() if is_norden(p))

    infos, info_errors = get_info(norden_ids)
    prices, price_errors = get_prices(norden_ids)
    stocks, stock_errors = get_stocks(norden_ids)

    info_by_id = {int(x.get("id") or x.get("product_id") or 0): x for x in infos}
    price_by_id = {int(x.get("product_id") or 0): x for x in prices}

    stock_by_id = {}
    for x in stocks:
        pid = int(x.get("product_id") or 0)
        if pid:
            stock_by_id.setdefault(pid, []).append(x)

    headers = [
        "Артикул", "Ozon product_id", "Название", "Бренд", "Архивный",
        "Категория Ozon", "Тип Ozon", "Ширина", "Высота", "Глубина",
        "Ед. габаритов", "Вес", "Ед. веса", "Основное фото", "Фото",
        "Цена", "Старая цена", "Минимальная цена", "Маркетинговая цена",
        "Остатки JSON", "Атрибуты JSON", "Комплексные атрибуты JSON",
        "API info JSON", "API list JSON", "API price JSON",
    ] + [f"Полный JSON {i}" for i in range(1,17)]

    rows = []
    for pid in norden_ids:
        a = attr_by_id.get(pid) or {}
        li = by_id.get(pid) or {}
        inf = info_by_id.get(pid) or {}
        pr = price_by_id.get(pid) or {}
        st = stock_by_id.get(pid) or []
        price = pr.get("price") or {}
        full = {
            "product_list": li,
            "attributes": a,
            "info": inf,
            "price": pr,
            "stocks": st,
            "archived": pid in archived_ids,
        }
        row = [
            str(a.get("offer_id") or li.get("offer_id") or inf.get("offer_id") or ""),
            pid,
            str(a.get("name") or inf.get("name") or ""),
            first_brand(a),
            "Да" if pid in archived_ids else "Нет",
            a.get("description_category_id") or "",
            a.get("type_id") or "",
            a.get("width") or "",
            a.get("height") or "",
            a.get("depth") or "",
            a.get("dimension_unit") or "",
            a.get("weight") or "",
            a.get("weight_unit") or "",
            a.get("primary_image") or "",
            j(a.get("images") or []),
            price.get("price") or "",
            price.get("old_price") or "",
            price.get("min_price") or "",
            price.get("marketing_seller_price") or "",
            j(st),
            j(a.get("attributes") or []),
            j(a.get("complex_attributes") or []),
            j(inf),
            j(li),
            j(pr),
        ] + split_json(full)
        rows.append(row)

    creds_info = json.loads(SA_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    gc = gspread.authorize(Credentials.from_service_account_info(creds_info, scopes=scopes))

    if SPREADSHEET_ID:
        sh = gc.open_by_key(SPREADSHEET_ID)
    else:
        matches = gc.openall(TITLE)
        if matches:
            sh = matches[0]
        else:
            sh = gc.create(TITLE)
            try:
                sh.share(OWNER_EMAIL, perm_type="user", role="writer", notify=False)
            except Exception:
                pass

    try:
        ws = sh.worksheet(SHEET)
    except gspread.WorksheetNotFound:
        worksheets = sh.worksheets()
        if len(worksheets) == 1 and worksheets[0].title in ("Sheet1", "Лист1"):
            ws = worksheets[0]
            ws.update_title(SHEET)
        else:
            ws = sh.add_worksheet(title=SHEET, rows=max(1000, len(rows)+50), cols=len(headers)+5)

    ws.clear()
    needed_rows = max(len(rows)+1, 100)
    needed_cols = len(headers)
    if ws.row_count < needed_rows or ws.col_count < needed_cols:
        ws.resize(rows=max(ws.row_count, needed_rows), cols=max(ws.col_count, needed_cols))

    ws.update(range_name=f"A1:{gspread.utils.rowcol_to_a1(len(rows)+1, len(headers))}",
              values=[headers] + rows, value_input_option="RAW")
    ws.freeze(rows=1)
    ws.set_basic_filter()

    report = {
        "ok": True,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "spreadsheet_title": TITLE,
        "worksheet": SHEET,
        "spreadsheet_url": sh.url,
        "all_unique_ozon_products": len(all_ids),
        "archived_products_scanned": len(archived_ids),
        "norden_products_exported": len(rows),
        "info_endpoint_errors": info_errors,
        "price_endpoint_errors": price_errors,
        "stock_endpoint_errors": stock_errors,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if info_errors or price_errors or stock_errors:
        print("WARNING: one or more optional Ozon enrichment endpoints returned errors")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
