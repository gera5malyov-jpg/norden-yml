#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone

import requests

BASEROW_URL = os.environ.get("BASEROW_URL", "http://147.78.67.6").rstrip("/")
BASEROW_TOKEN = os.environ.get("BASEROW_DATABASE_TOKEN", "").strip()
NORDEN_SECRET = os.environ.get("NORDEN_SECRET", "").strip()

CATALOG_TABLE_ID = 156
SUPPLIERS_TABLE_ID = 157
SUPPLIER_NAME = "Norden"

NORDEN_API = "https://norden.group/api-products/"
PRICE_XML_URL = "https://norden.group/index.php?dispatch=sw_user_prices.get_file&file=Norden.group+-K8%25.xml"
NORDEN_CATEGORIES_API = "https://norden.group/api-categories/"

FIELD_PURCHASE = "Закупка"
FIELD_RRP = "РРЦ поставщика"
FIELD_STOCK = "Остаток"

def s(v):
    return str(v or "").strip()

def norm(v):
    return unicodedata.normalize("NFKC", s(v)).casefold()

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def dec(v):
    x = s(v).replace("\xa0", " ").replace(" ", "").replace(",", ".")
    if not x:
        return None
    try:
        return float(x)
    except Exception:
        return None

def stock_num(v):
    x = s(v).replace("\xa0", " ")
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

def is_excluded(item):
    hay = " | ".join([
        s(item.get("name")),
        s(item.get("category")),
        s(item.get("group")),
        s(item.get("description")),
    ]).casefold()
    if "уцен" in hay:
        return True, "discount"
    if "для моск" in hay or re.search(r"только.{0,30}моск", hay):
        return True, "moscow"
    return False, None

class Baserow:
    def __init__(self, base_url, token):
        if not token:
            raise RuntimeError("BASEROW_DATABASE_TOKEN is missing")
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "norden-database-sync/1.0",
        })

    def request(self, method, path, **kwargs):
        url = self.base_url + path
        last = None
        for attempt in range(6):
            try:
                r = self.session.request(method, url, timeout=60, **kwargs)
                if r.status_code >= 500:
                    last = RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
                    time.sleep(min(2 ** attempt, 20))
                    continue
                if not r.ok:
                    raise RuntimeError(f"Baserow {method} {path} -> HTTP {r.status_code}: {r.text[:1000]}")
                if r.status_code == 204 or not r.text:
                    return None
                return r.json()
            except requests.RequestException as exc:
                last = exc
                time.sleep(min(2 ** attempt, 20))
        raise RuntimeError(f"Baserow request failed: {last}")

    def fields(self, table_id):
        return self.request("GET", f"/api/database/fields/table/{table_id}/") or []

    def ensure_number_field(self, table_id, name, decimals):
        fields = self.fields(table_id)
        found = [f for f in fields if s(f.get("name")) == name]
        if found:
            if found[0].get("type") != "number":
                raise RuntimeError(f"Field {name!r} exists but is not a number")
            return found[0]
        body = {
            "name": name,
            "type": "number",
            "number_decimal_places": int(decimals),
            "number_negative": False,
        }
        return self.request(
            "POST",
            f"/api/database/fields/table/{table_id}/",
            data=json.dumps(body, ensure_ascii=False),
        )

    def all_rows(self, table_id):
        out = []
        page = 1
        while True:
            payload = self.request(
                "GET",
                f"/api/database/rows/table/{table_id}/?user_field_names=true&size=200&page={page}",
            )
            batch = payload.get("results", []) if isinstance(payload, dict) else []
            out.extend(batch)
            if not payload.get("next"):
                break
            page += 1
        return out

    def create_row(self, table_id, body):
        return self.request(
            "POST",
            f"/api/database/rows/table/{table_id}/?user_field_names=true",
            data=json.dumps(body, ensure_ascii=False),
        )

    def update_row(self, table_id, row_id, body):
        return self.request(
            "PATCH",
            f"/api/database/rows/table/{table_id}/{row_id}/?user_field_names=true",
            data=json.dumps(body, ensure_ascii=False),
        )

    def batch_create_rows(self, table_id, bodies, batch_size=100):
        for start in range(0, len(bodies), batch_size):
            batch = bodies[start:start + batch_size]
            try:
                self.request(
                    "POST",
                    f"/api/database/rows/table/{table_id}/batch/?user_field_names=true",
                    data=json.dumps({"items": batch}, ensure_ascii=False),
                )
            except Exception:
                for body in batch:
                    self.create_row(table_id, body)

    def batch_update_rows(self, table_id, bodies, batch_size=100):
        for start in range(0, len(bodies), batch_size):
            batch = bodies[start:start + batch_size]
            try:
                self.request(
                    "PATCH",
                    f"/api/database/rows/table/{table_id}/batch/?user_field_names=true",
                    data=json.dumps({"items": batch}, ensure_ascii=False),
                )
            except Exception:
                for body in batch:
                    row_id = body["id"]
                    single = {k: v for k, v in body.items() if k != "id"}
                    self.update_row(table_id, row_id, single)

def request_with_retry(url, *, headers=None, params=None, timeout=120, attempts=6):
    last = None
    for attempt in range(attempts):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
                time.sleep(min(8 * (attempt + 1), 45))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            last = exc
            time.sleep(min(5 * (attempt + 1), 30))
    raise RuntimeError(f"GET failed for {url}: {last}")

def load_category_paths():
    r = request_with_retry(
        NORDEN_CATEGORIES_API,
        headers={"secret": NORDEN_SECRET} if NORDEN_SECRET else None,
        timeout=60,
    )
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
            parent = s(row.get("parent_id"))
            cur = "" if parent in ("", "0") else parent
        out.reverse()
        cache[cid] = out
        return out
    return chain

def load_api():
    if not NORDEN_SECRET:
        raise RuntimeError("NORDEN_SECRET is missing")
    try:
        category_chain = load_category_paths()
    except Exception:
        category_chain = None
    rows = []
    page = 1
    last_call = 0.0
    while True:
        if page > 1:
            delay = 6.2 - (time.monotonic() - last_call)
            if delay > 0:
                time.sleep(delay)
        r = request_with_retry(
            NORDEN_API,
            headers={"secret": NORDEN_SECRET},
            params={"page": page},
            timeout=90,
        )
        last_call = time.monotonic()
        data = r.json()
        batch = data.get("products") or []
        rows.extend(x for x in batch if isinstance(x, dict))
        pd = data.get("page_data") or {}
        total = int(str(pd.get("total_items") or 0).replace(" ", "") or 0)
        per_page = int(str(pd.get("items_per_page") or len(batch) or 500))
        if not batch or len(rows) >= total or len(batch) < per_page:
            break
        page += 1

    products = {}
    duplicates = []
    for raw in rows:
        article = s(raw.get("product_code"))
        if not article:
            continue
        raw_categories = [s(x) for x in s(raw.get("category")).split(",") if s(x)]
        category_parts = []
        if category_chain:
            for cid in raw_categories:
                path_parts = category_chain(cid)
                if path_parts:
                    category_parts.append(" > ".join(path_parts))
        item = {
            "article": article,
            "name": s(raw.get("name")) or article,
            "category": " | ".join(category_parts) or s(raw.get("category")),
            "description": s(raw.get("description")),
            "purchase": dec(raw.get("price")),
            "rrp": dec(raw.get("price_rrc")),
            "stock": stock_num(raw.get("qty")),
            "source": "api",
        }
        k = norm(article)
        if k in products:
            duplicates.append(article)
            old = products[k]
            if (item.get("stock") or 0) > (old.get("stock") or 0):
                old["stock"] = item.get("stock")
            if old.get("purchase") is None and item.get("purchase") is not None:
                old["purchase"] = item.get("purchase")
            if not old.get("name") and item.get("name"):
                old["name"] = item.get("name")
        else:
            products[k] = item
    return products, duplicates

def load_price_xml():
    raw = request_with_retry(PRICE_XML_URL, timeout=180).content
    root = ET.fromstring(raw)
    products = {}
    duplicates = []
    for n in root.iter("Номенклатура"):
        article = s(n.findtext("Артикул"))
        if not article:
            continue
        prices = {}
        for p in n.findall("Цена"):
            kind = norm(p.attrib.get("ВидЦен"))
            if kind:
                prices[kind] = dec(p.text)
        stocks = {}
        for st in n.findall("СвободныйОстаток"):
            wh = s(st.attrib.get("Склад"))
            if wh:
                stocks[wh] = stock_num(st.text)

        stock = stocks.get("Основной склад")
        item = {
            "article": article,
            "name": s(n.findtext("НаименованиеПолное")) or s(n.findtext("Наименование")) or s(n.findtext("Ссылка")) or article,
            "category": "",
            "group": s(n.findtext("Группа")),
            "description": "",
            "purchase": prices.get("опт"),
            "rrp": prices.get("ррц"),
            "stock": stock,
            "source": "price_xml",
        }
        k = norm(article)
        if k in products:
            duplicates.append(article)
            old = products[k]
            if (item.get("stock") or 0) > (old.get("stock") or 0):
                old["stock"] = item.get("stock")
            if old.get("purchase") is None and item.get("purchase") is not None:
                old["purchase"] = item.get("purchase")
            if old.get("rrp") is None and item.get("rrp") is not None:
                old["rrp"] = item.get("rrp")
        else:
            products[k] = item
    return products, duplicates

def load_source():
    api_error = None
    try:
        source, duplicates = load_api()
        if len(source) < 1000:
            raise RuntimeError(f"Safety stop: API returned only {len(source)} unique products")
        source_kind = "api"
        return source, duplicates, source_kind, None
    except Exception as exc:
        api_error = str(exc)

    source, duplicates = load_price_xml()
    if len(source) < 1000:
        raise RuntimeError(f"Safety stop: fallback price catalog returned only {len(source)} unique products; API error: {api_error}")
    return source, duplicates, "price_xml_fallback", api_error

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report = {
        "started_at": now_iso(),
        "dry_run": args.dry_run,
        "source": None,
        "api_error": None,
        "source_unique": 0,
        "source_duplicate_codes": 0,
        "eligible_in_stock": 0,
        "excluded_discount": 0,
        "excluded_moscow": 0,
        "existing_matches": 0,
        "existing_price_stock_updates": 0,
        "existing_out_of_stock_updates": 0,
        "new_rows_created": 0,
        "database_duplicate_keys": [],
        "skipped_ambiguous": 0,
        "errors": [],
    }

    source, source_duplicates, source_kind, api_error = load_source()
    report["source"] = source_kind
    report["api_error"] = api_error
    report["source_unique"] = len(source)
    report["source_duplicate_codes"] = len(set(norm(x) for x in source_duplicates))

    br = Baserow(BASEROW_URL, BASEROW_TOKEN)

    if not args.dry_run:
        br.ensure_number_field(CATALOG_TABLE_ID, FIELD_PURCHASE, 2)
        br.ensure_number_field(CATALOG_TABLE_ID, FIELD_RRP, 2)
        br.ensure_number_field(CATALOG_TABLE_ID, FIELD_STOCK, 0)

    supplier_rows = br.all_rows(SUPPLIERS_TABLE_ID)
    norden_suppliers = [r for r in supplier_rows if norm(r.get("Поставщик")) == norm(SUPPLIER_NAME)]
    if len(norden_suppliers) != 1:
        raise RuntimeError(f"Expected exactly one supplier Norden row, found {len(norden_suppliers)}")
    supplier_id = int(norden_suppliers[0]["id"])

    catalog_rows = br.all_rows(CATALOG_TABLE_ID)
    by_code = defaultdict(list)
    for row in catalog_rows:
        code = norm(row.get("Наименование артикула"))
        if code:
            by_code[code].append(row)

    duplicate_db = {k: rows for k, rows in by_code.items() if len(rows) > 1}
    report["database_duplicate_keys"] = [
        {"code": k, "row_ids": [r.get("id") for r in rows]}
        for k, rows in list(duplicate_db.items())[:200]
    ]

    pending_updates = []
    pending_creates = []

    for k, item in source.items():
        excluded, reason = is_excluded(item)
        if excluded:
            if reason == "discount":
                report["excluded_discount"] += 1
            elif reason == "moscow":
                report["excluded_moscow"] += 1
            continue

        stock = item.get("stock")
        in_stock = stock is not None and stock > 0
        if in_stock:
            report["eligible_in_stock"] += 1

        existing = by_code.get(k, [])
        if len(existing) > 1:
            report["skipped_ambiguous"] += 1
            continue

        if existing:
            report["existing_matches"] += 1
            row = existing[0]
            body = {}
            if item.get("purchase") is not None:
                body[FIELD_PURCHASE] = item["purchase"]
            if item.get("rrp") is not None:
                body[FIELD_RRP] = item["rrp"]
            if stock is not None:
                body[FIELD_STOCK] = stock
                body["Наличие"] = bool(stock > 0)

            if body:
                changed = False
                for field, value in body.items():
                    current = row.get(field)
                    if field == "Наличие":
                        if bool(current) != bool(value):
                            changed = True
                    elif current in (None, ""):
                        changed = True
                    else:
                        try:
                            if float(current) != float(value):
                                changed = True
                        except Exception:
                            if s(current) != s(value):
                                changed = True
                if changed:
                    if not args.dry_run:
                        pending_updates.append({"id": row["id"], **body})
                    report["existing_price_stock_updates"] += 1
                    if stock == 0:
                        report["existing_out_of_stock_updates"] += 1
            continue

        if not in_stock:
            continue

        body = {
            "Название": item.get("name") or item.get("article"),
            "Наименование артикула": item.get("article"),
            "Поставщик": [supplier_id],
            "Наличие": True,
        }
        if item.get("purchase") is not None:
            body[FIELD_PURCHASE] = item["purchase"]
        if item.get("rrp") is not None:
            body[FIELD_RRP] = item["rrp"]
        if stock is not None:
            body[FIELD_STOCK] = stock

        if not args.dry_run:
            pending_creates.append(body)
        report["new_rows_created"] += 1

    if not args.dry_run:
        if pending_updates:
            br.batch_update_rows(CATALOG_TABLE_ID, pending_updates)
        if pending_creates:
            br.batch_create_rows(CATALOG_TABLE_ID, pending_creates)

    report["finished_at"] = now_iso()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["skipped_ambiguous"]:
        print("WARNING: ambiguous duplicate Database keys were skipped", flush=True)

if __name__ == "__main__":
    main()
