#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

OZON_BASE = "https://api-seller.ozon.ru"
BASEROW_URL = os.environ.get("BASEROW_URL", "http://147.78.67.6").rstrip("/")
BASEROW_TOKEN = os.environ.get("BASEROW_DATABASE_TOKEN", "").strip()
CATALOG_TABLE_ID = 156
SUPPLIERS_TABLE_ID = 157
SUPPLIER_NAME = "Norden"
BRAND_ATTR_ID = 85
REPORT = Path("baserow/ozon_norden_database_report.json")

IMAGE_KEY_RE = re.compile(r"(?:image|images|primary_image|photo|picture|media|rich_content)", re.I)
IMAGE_ATTR_RE = re.compile(r"(?:изображ|фото|картин|image|photo|picture)", re.I)

BASE_FIELD_SPECS = {
    "Ozon ID товара": ("number", 0),
    "Ozon Название": ("text", None),
    "Ozon Бренд": ("text", None),
    "Ozon Архив": ("boolean", None),
    "Ozon Категория": ("text", None),
    "Ozon ID категории": ("number", 0),
    "Ozon ID типа": ("number", 0),
    "Ozon Описание": ("long_text", None),
    "Ozon Штрихкоды": ("long_text", None),
    "Ozon Цена": ("number", 2),
    "Ozon Старая цена": ("number", 2),
    "Ozon Маркетинговая цена": ("number", 2),
    "Ozon Остаток": ("number", 0),
    "Ozon Статус": ("text", None),
    "Ozon Видимость": ("text", None),
    "Ozon Обновлено": ("text", None),
    "Ozon данные без изображений (JSON)": ("long_text", None),
}

def s(v):
    return str(v or "").strip()

def norm(v):
    return unicodedata.normalize("NFKC", s(v)).casefold().strip()

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    try:
        return float(str(v).replace("\xa0", "").replace(" ", "").replace(",", "."))
    except Exception:
        return None

def chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]

class Ozon:
    def __init__(self):
        client_id = os.environ.get("OZON_CLIENT_ID", "").strip()
        api_key = os.environ.get("OZON_API_KEY", "").strip()
        if not client_id or not api_key:
            raise RuntimeError("OZON_CLIENT_ID/OZON_API_KEY are missing")
        self.session = requests.Session()
        self.headers = {
            "Client-Id": client_id,
            "Api-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "megapolis-ozon-norden-to-baserow/1.0",
        }

    def post(self, path, body, attempts=8):
        last = None
        for attempt in range(attempts):
            try:
                r = self.session.post(OZON_BASE + path, headers=self.headers, json=body, timeout=120)
                last = r
                if r.status_code == 429 or r.status_code >= 500:
                    time.sleep(float(r.headers.get("Retry-After") or min(30, 2 ** attempt)))
                    continue
                if not r.ok:
                    raise RuntimeError(f"Ozon {path}: HTTP {r.status_code}: {r.text[:1500]}")
                return r.json() if r.content else {}
            except requests.RequestException as exc:
                last = exc
                time.sleep(min(30, 2 ** attempt))
        raise RuntimeError(f"Ozon {path}: retries exhausted: {last}")

class Baserow:
    def __init__(self):
        if not BASEROW_TOKEN:
            raise RuntimeError("BASEROW_DATABASE_TOKEN is missing")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token {BASEROW_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "megapolis-ozon-norden-to-baserow/1.0",
        })
        self.schema_session = self._make_schema_session()

    def _make_schema_session(self):
        pairs = [
            ("BASEROW_ADMIN_EMAIL", "BASEROW_ADMIN_PASSWORD"),
            ("BASEROW_EMAIL", "BASEROW_PASSWORD"),
            ("BASEROW_DB_EMAIL", "BASEROW_DB_PASSWORD"),
            ("DATABASE_ADMIN_EMAIL", "DATABASE_ADMIN_PASSWORD"),
            ("DATABASE_EMAIL", "DATABASE_PASSWORD"),
            ("DATABASE_USER_EMAIL", "DATABASE_USER_PASSWORD"),
            ("DB_EMAIL", "DB_PASSWORD"),
        ]
        for user_key, pass_key in pairs:
            username = os.environ.get(user_key, "").strip()
            password = os.environ.get(pass_key, "").strip()
            if not username or not password:
                continue
            for login_key in ("username", "email"):
                try:
                    r = requests.post(
                        BASEROW_URL + "/api/user/token-auth/",
                        json={login_key: username, "password": password},
                        timeout=30,
                    )
                    if not r.ok:
                        continue
                    data = r.json() if r.content else {}
                    token = s(data.get("token") or data.get("access_token") or data.get("access"))
                    if token:
                        session = requests.Session()
                        session.headers.update({
                            "Authorization": f"JWT {token}",
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                            "User-Agent": "megapolis-ozon-norden-schema/1.0",
                        })
                        return session
                except Exception:
                    continue
        return None

    def schema_request(self, method, path, **kwargs):
        if self.schema_session is None:
            return self.request(method, path, **kwargs)
        r = self.schema_session.request(method, BASEROW_URL + path, timeout=120, **kwargs)
        if not r.ok:
            raise RuntimeError(f"Baserow JWT {method} {path}: HTTP {r.status_code}: {r.text[:1500]}")
        if r.status_code == 204 or not r.text:
            return None
        return r.json()

    def request(self, method, path, **kwargs):
        last = None
        for attempt in range(7):
            try:
                r = self.session.request(method, BASEROW_URL + path, timeout=120, **kwargs)
                if r.status_code == 429 or r.status_code >= 500:
                    last = RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
                    time.sleep(min(30, 2 ** attempt))
                    continue
                if not r.ok:
                    raise RuntimeError(f"Baserow {method} {path}: HTTP {r.status_code}: {r.text[:1500]}")
                if r.status_code == 204 or not r.text:
                    return None
                return r.json()
            except requests.RequestException as exc:
                last = exc
                time.sleep(min(30, 2 ** attempt))
        raise RuntimeError(f"Baserow {method} {path}: retries exhausted: {last}")

    def fields(self, table_id):
        return self.request("GET", f"/api/database/fields/table/{table_id}/") or []

    def all_rows(self, table_id):
        out, page = [], 1
        while True:
            d = self.request(
                "GET",
                f"/api/database/rows/table/{table_id}/?user_field_names=true&size=200&page={page}",
            )
            rows = d.get("results") or []
            out.extend(rows)
            if not d.get("next"):
                break
            page += 1
        return out

    def ensure_field(self, table_id, name, field_type, decimals=None):
        fields = self.fields(table_id)
        for f in fields:
            if s(f.get("name")) == name:
                return f, False
        body = {"name": name, "type": field_type}
        if field_type == "number":
            body.update({
                "number_decimal_places": int(decimals or 0),
                "number_negative": True,
            })
        f = self.schema_request(
            "POST",
            f"/api/database/fields/table/{table_id}/",
            data=json.dumps(body, ensure_ascii=False),
        )
        return f, True

    def batch_update_rows(self, table_id, bodies, batch_size=50):
        for batch in chunks(bodies, batch_size):
            try:
                self.request(
                    "PATCH",
                    f"/api/database/rows/table/{table_id}/batch/?user_field_names=true",
                    data=json.dumps({"items": batch}, ensure_ascii=False),
                )
            except Exception:
                for body in batch:
                    row_id = body["id"]
                    payload = {k: v for k, v in body.items() if k != "id"}
                    self.request(
                        "PATCH",
                        f"/api/database/rows/table/{table_id}/{row_id}/?user_field_names=true",
                        data=json.dumps(payload, ensure_ascii=False),
                    )

def list_visibility(oz, visibility):
    out, last_id, seen = [], "", set()
    while True:
        body = {"filter": {"visibility": visibility}, "limit": 1000}
        if last_id:
            body["last_id"] = last_id
        d = oz.post("/v3/product/list", body)
        r = d.get("result") or {}
        items = r.get("items") or []
        out.extend(items)
        nxt = s(r.get("last_id"))
        total = int(r.get("total") or 0)
        if not items or len(out) >= total or not nxt or nxt == last_id or nxt in seen:
            break
        seen.add(last_id)
        last_id = nxt
    return out

def get_attributes(oz, ids):
    out = []
    for batch in chunks(ids, 1000):
        d = oz.post("/v4/product/info/attributes", {
            "filter": {"product_id": batch, "visibility": "ALL"},
            "limit": 1000,
        })
        out.extend(d.get("result") or [])
    return out

def get_info(oz, ids):
    out = []
    for batch in chunks(ids, 1000):
        d = oz.post("/v3/product/info/list", {"product_id": batch})
        out.extend(d.get("items") or (d.get("result") or {}).get("items") or [])
    return out

def get_prices(oz, ids):
    out = []
    for batch in chunks(ids, 100):
        cursor = ""
        while True:
            body = {"filter": {"product_id": batch, "visibility": "ALL"}, "limit": 100}
            if cursor:
                body["cursor"] = cursor
            d = oz.post("/v5/product/info/prices", body)
            items = d.get("items") or []
            out.extend(items)
            nxt = s(d.get("cursor"))
            if not items or not nxt or nxt == cursor:
                break
            cursor = nxt
    return out

def get_stocks(oz, ids):
    out = []
    for batch in chunks(ids, 100):
        cursor = ""
        while True:
            body = {"filter": {"product_id": batch, "visibility": "ALL"}, "limit": 100}
            if cursor:
                body["cursor"] = cursor
            d = oz.post("/v4/product/info/stocks", body)
            items = d.get("items") or []
            out.extend(items)
            nxt = s(d.get("cursor"))
            if not items or not nxt or nxt == cursor:
                break
            cursor = nxt
    return out

def brand_values(card):
    for a in card.get("attributes") or []:
        if int(a.get("id") or a.get("attribute_id") or 0) == BRAND_ATTR_ID:
            return [s(v.get("value")) for v in (a.get("values") or []) if isinstance(v, dict) and s(v.get("value"))]
    return []

def category_paths(oz):
    d = oz.post("/v1/description-category/tree", {"language": "DEFAULT"})
    result = {}
    def walk(nodes, prefix):
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            name = s(n.get("category_name"))
            cid = n.get("description_category_id")
            path = prefix + ([name] if name else [])
            if cid:
                result[int(cid)] = " > ".join(path)
            walk(n.get("children") or [], path)
    walk(d.get("result") or [], [])
    return result

def attribute_schema(oz, category_id, type_id, cache):
    key = (int(category_id or 0), int(type_id or 0))
    if not all(key):
        return {}
    if key in cache:
        return cache[key]
    d = oz.post("/v1/description-category/attribute", {
        "description_category_id": key[0],
        "type_id": key[1],
        "language": "DEFAULT",
    })
    rows = {int(x.get("id") or 0): x for x in (d.get("result") or []) if int(x.get("id") or 0)}
    cache[key] = rows
    return rows

def join_values(values):
    out = []
    for v in values or []:
        val = s(v.get("value")) if isinstance(v, dict) else s(v)
        if val and val not in out:
            out.append(val)
    return ", ".join(out)

def collect_characteristics(card, schema):
    by_id = defaultdict(list)
    def add_attr(a):
        if not isinstance(a, dict):
            return
        aid = int(a.get("id") or a.get("attribute_id") or 0)
        if not aid:
            return
        meta = schema.get(aid) or {}
        name = s(meta.get("name")) or f"Характеристика {aid}"
        if IMAGE_ATTR_RE.search(name):
            return
        value = join_values(a.get("values") or [])
        if value and value not in by_id[aid]:
            by_id[aid].append(value)
    for a in card.get("attributes") or []:
        add_attr(a)
    for group in card.get("complex_attributes") or []:
        if isinstance(group, dict):
            for a in group.get("attributes") or []:
                add_attr(a)
        elif isinstance(group, list):
            for a in group:
                add_attr(a)
    out = []
    for aid, values in by_id.items():
        meta = schema.get(aid) or {}
        name = s(meta.get("name")) or f"Характеристика {aid}"
        out.append({"id": aid, "name": name, "value": " | ".join(values)})
    return out

def strip_image_keys(value):
    if isinstance(value, dict):
        return {k: strip_image_keys(v) for k, v in value.items() if not IMAGE_KEY_RE.search(s(k))}
    if isinstance(value, list):
        return [strip_image_keys(v) for v in value]
    return value

def strip_image_attributes(card, schema):
    if not isinstance(card, dict):
        return card
    out = {}
    for k, v in card.items():
        if IMAGE_KEY_RE.search(s(k)):
            continue
        if k == "attributes" and isinstance(v, list):
            kept = []
            for a in v:
                if not isinstance(a, dict):
                    kept.append(a)
                    continue
                aid = int(a.get("id") or a.get("attribute_id") or 0)
                name = s((schema.get(aid) or {}).get("name"))
                if name and IMAGE_ATTR_RE.search(name):
                    continue
                kept.append(strip_image_keys(a))
            out[k] = kept
        elif k == "complex_attributes" and isinstance(v, list):
            groups = []
            for group in v:
                if not isinstance(group, dict):
                    groups.append(strip_image_keys(group))
                    continue
                g = {}
                for gk, gv in group.items():
                    if IMAGE_KEY_RE.search(s(gk)):
                        continue
                    if gk == "attributes" and isinstance(gv, list):
                        attrs = []
                        for a in gv:
                            if not isinstance(a, dict):
                                attrs.append(a)
                                continue
                            aid = int(a.get("id") or a.get("attribute_id") or 0)
                            name = s((schema.get(aid) or {}).get("name"))
                            if name and IMAGE_ATTR_RE.search(name):
                                continue
                            attrs.append(strip_image_keys(a))
                        g[gk] = attrs
                    else:
                        g[gk] = strip_image_keys(gv)
                groups.append(g)
            out[k] = groups
        else:
            out[k] = strip_image_keys(v)
    return out

def stock_total(stock_rows):
    total = 0
    found = False
    for row in stock_rows or []:
        if not isinstance(row, dict):
            continue
        stocks = row.get("stocks")
        if isinstance(stocks, list):
            for st in stocks:
                if not isinstance(st, dict):
                    continue
                v = num(st.get("present"))
                if v is not None:
                    total += int(v)
                    found = True
        else:
            v = num(row.get("present"))
            if v is not None:
                total += int(v)
                found = True
    return total if found else 0

def barcodes(info, card):
    vals = []
    candidates = []
    candidates.extend(info.get("barcodes") or [])
    candidates.extend(card.get("barcodes") or [])
    for x in candidates:
        val = s(x.get("barcode") or x.get("value")) if isinstance(x, dict) else s(x)
        if val and val not in vals:
            vals.append(val)
    return "\n".join(vals)

def status_text(info):
    st = info.get("statuses") or {}
    vals = []
    for key in ("status_name", "status_description", "moderate_status", "validation_status", "status"):
        val = s(st.get(key) or info.get(key))
        if val and val not in vals:
            vals.append(val)
    return " | ".join(vals)

def visibility_text(list_row, info):
    vals = []
    for source in (list_row, info):
        for key in ("visibility", "visible", "is_visible", "state"):
            if key in source and source.get(key) not in (None, ""):
                val = s(source.get(key))
                if val and val not in vals:
                    vals.append(val)
    return " | ".join(vals)

def supplier_ids(row):
    out = set()
    for x in row.get("Поставщик") or []:
        if isinstance(x, dict) and x.get("id") is not None:
            try:
                out.add(int(x["id"]))
            except Exception:
                pass
    return out

def safe_field_name(name, aid, collisions):
    base = "Ozon | " + re.sub(r"\s+", " ", s(name)).strip()
    if collisions.get(norm(name), 0) > 1:
        base += f" [{aid}]"
    if len(base) > 180:
        base = base[:165].rstrip() + f"… [{aid}]"
    return base

def field_key(value):
    return re.sub(r"[^0-9a-zа-яё]+", "", norm(value))

def write_report(report):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def main():
    report = {
        "started_at": now_iso(),
        "source": "Ozon Seller API",
        "filter": "brand=Norden",
        "matching": "Ozon offer_id -> Database Наименование артикула + Поставщик=Norden",
        "images_written": 0,
        "ozon_total_unique": 0,
        "ozon_norden_total": 0,
        "ozon_norden_archived": 0,
        "database_norden_rows": 0,
        "matched_rows": 0,
        "updated_rows": 0,
        "unmatched_ozon": 0,
        "unmatched_offer_ids": [],
        "ambiguous_database_matches": [],
        "supplier_conflicts": [],
        "base_fields_created": [],
        "characteristic_fields_created": [],
        "characteristic_fields_total": 0,
        "characteristic_values_written": 0,
        "errors": [],
    }
    write_report(report)
    try:
        oz = Ozon()
        br = Baserow()

        active = list_visibility(oz, "ALL")
        archived = list_visibility(oz, "ARCHIVED")
        archived_ids = {int(x.get("product_id") or 0) for x in archived if int(x.get("product_id") or 0)}
        by_id = {}
        for x in active + archived:
            pid = int(x.get("product_id") or 0)
            if pid:
                by_id[pid] = x
        all_ids = sorted(by_id)
        report["ozon_total_unique"] = len(all_ids)

        attrs = get_attributes(oz, all_ids)
        attr_by_id = {int(x.get("id") or x.get("product_id") or 0): x for x in attrs if int(x.get("id") or x.get("product_id") or 0)}
        norden_ids = sorted(pid for pid, card in attr_by_id.items() if any(norm(v) == "norden" for v in brand_values(card)))
        report["ozon_norden_total"] = len(norden_ids)
        report["ozon_norden_archived"] = sum(1 for pid in norden_ids if pid in archived_ids)

        infos = get_info(oz, norden_ids)
        prices = get_prices(oz, norden_ids)
        stocks = get_stocks(oz, norden_ids)
        info_by_id = {int(x.get("id") or x.get("product_id") or 0): x for x in infos if int(x.get("id") or x.get("product_id") or 0)}
        price_by_id = {int(x.get("product_id") or 0): x for x in prices if int(x.get("product_id") or 0)}
        stock_by_id = defaultdict(list)
        for x in stocks:
            pid = int(x.get("product_id") or 0)
            if pid:
                stock_by_id[pid].append(x)

        try:
            cat_paths = category_paths(oz)
        except Exception as exc:
            cat_paths = {}
            report["errors"].append({"stage": "category_tree", "error": str(exc)[:1000]})

        supplier_rows = br.all_rows(SUPPLIERS_TABLE_ID)
        norden_suppliers = [r for r in supplier_rows if norm(r.get("Поставщик")) == norm(SUPPLIER_NAME)]
        if len(norden_suppliers) != 1:
            raise RuntimeError(f"Expected exactly one supplier Norden row, found {len(norden_suppliers)}")
        supplier_id = int(norden_suppliers[0]["id"])

        catalog_rows = br.all_rows(CATALOG_TABLE_ID)
        norden_rows = [r for r in catalog_rows if supplier_id in supplier_ids(r)]
        report["database_norden_rows"] = len(norden_rows)
        report["database_fields"] = [
            {"name": s(x.get("name")), "type": s(x.get("type")), "id": x.get("id")}
            for x in br.fields(CATALOG_TABLE_ID)
        ]
        write_report(report)
        match_fields = ("Артикул", "Артикул KIT", "Код для сайта", "Наименование артикула", "Артикул поставщика")
        norden_indexes = {field: defaultdict(list) for field in match_fields}
        all_indexes = {field: defaultdict(list) for field in match_fields}
        for r in catalog_rows:
            for field in match_fields:
                key = norm(r.get(field))
                if key:
                    all_indexes[field][key].append(r)
        for r in norden_rows:
            for field in match_fields:
                key = norm(r.get(field))
                if key:
                    norden_indexes[field][key].append(r)
        report["matched_by_field"] = {field: 0 for field in match_fields}

        schema_cache = {}
        char_rows_by_pid = {}
        name_to_ids = defaultdict(set)
        schema_by_pid = {}
        for idx, pid in enumerate(norden_ids, 1):
            card = attr_by_id.get(pid) or {}
            info = info_by_id.get(pid) or {}
            dc = card.get("description_category_id") or info.get("description_category_id")
            tid = card.get("type_id") or info.get("type_id")
            try:
                schema = attribute_schema(oz, dc, tid, schema_cache) if dc and tid else {}
            except Exception as exc:
                schema = {}
                report["errors"].append({"stage": "attribute_schema", "product_id": pid, "error": str(exc)[:1000]})
            schema_by_pid[pid] = schema
            chars = collect_characteristics(card, schema)
            char_rows_by_pid[pid] = chars
            for c in chars:
                name_to_ids[norm(c["name"])].add(int(c["id"]))
            if idx % 50 == 0:
                write_report(report)

        collisions = {k: len(v) for k, v in name_to_ids.items()}

        characteristic_summary = {}
        for pid, chars in char_rows_by_pid.items():
            for item in chars:
                key = str(int(item["id"]))
                row = characteristic_summary.setdefault(key, {
                    "id": int(item["id"]),
                    "name": item["name"],
                    "products": 0,
                    "sample_values": [],
                })
                row["products"] += 1
                value = s(item.get("value"))
                if value and value not in row["sample_values"] and len(row["sample_values"]) < 5:
                    row["sample_values"].append(value)
        report["ozon_characteristics_summary"] = sorted(
            characteristic_summary.values(),
            key=lambda x: (norm(x["name"]), x["id"]),
        )
        report["ozon_characteristics_unique"] = len(characteristic_summary)
        write_report(report)

        db_fields = br.fields(CATALOG_TABLE_ID)
        existing_names = {s(x.get("name")) for x in db_fields}
        existing_by_key = defaultdict(list)
        for fld in db_fields:
            if s(fld.get("name")):
                existing_by_key[field_key(fld.get("name"))].append(fld)

        schema_create = br.schema_session is not None
        report["schema_mode"] = "jwt_create_fields" if schema_create else "existing_fields_only"
        report["base_fields_used"] = []
        report["technical_data_not_written_due_schema_permission"] = not schema_create

        if schema_create:
            for field_name, (field_type, decimals) in BASE_FIELD_SPECS.items():
                _, created = br.ensure_field(CATALOG_TABLE_ID, field_name, field_type, decimals)
                if created:
                    report["base_fields_created"].append(field_name)
                report["base_fields_used"].append(field_name)
        else:
            for field_name in ("Название", "Категория", "Цена Ozon"):
                if field_name in existing_names:
                    report["base_fields_used"].append(field_name)

        char_field_map = {}
        unique_chars = {}
        for chars in char_rows_by_pid.values():
            for item in chars:
                unique_chars[(int(item["id"]), norm(item["name"]))] = item["name"]

        if schema_create:
            for (aid, _), name in sorted(unique_chars.items(), key=lambda x: (norm(x[1]), x[0][0])):
                field_name = safe_field_name(name, aid, collisions)
                _, created = br.ensure_field(CATALOG_TABLE_ID, field_name, "long_text")
                char_field_map[(aid, norm(name))] = field_name
                if created:
                    report["characteristic_fields_created"].append(field_name)
        else:
            for (aid, _), name in sorted(unique_chars.items(), key=lambda x: (norm(x[1]), x[0][0])):
                protected_fields = {
                    "Название", "Категория", "Цена Ozon", "Артикул", "Артикул KIT",
                    "Код для сайта", "Наименование артикула", "Артикул поставщика",
                    "Поставщик", "Наличие", "Закупка Norden", "Остаток Norden",
                    "Первое изображение", "Первое изображение URL", "Все изображения",
                }
                candidates = [
                    x for x in existing_by_key.get(field_key(name), [])
                    if s(x.get("type")) in ("text", "long_text", "number")
                    and s(x.get("name")) not in protected_fields
                ]
                if len(candidates) == 1:
                    char_field_map[(aid, norm(name))] = s(candidates[0].get("name"))

        report["characteristic_fields_total"] = len(char_field_map)
        report["existing_characteristic_fields_used"] = sorted(set(char_field_map.values()))
        write_report(report)

        updates = []
        updated_at = now_iso()
        for idx, pid in enumerate(norden_ids, 1):
            card = attr_by_id.get(pid) or {}
            info = info_by_id.get(pid) or {}
            price_row = price_by_id.get(pid) or {}
            list_row = by_id.get(pid) or {}
            offer_id = s(card.get("offer_id") or list_row.get("offer_id") or info.get("offer_id"))
            if not offer_id:
                report["errors"].append({"stage": "match", "product_id": pid, "error": "offer_id missing"})
                continue
            key = norm(offer_id)
            matched_rows_by_id = {}
            matched_fields = []
            for field in match_fields:
                for candidate in norden_indexes[field].get(key) or []:
                    rid = int(candidate.get("id"))
                    matched_rows_by_id[rid] = candidate
                    if field not in matched_fields:
                        matched_fields.append(field)
            matches = list(matched_rows_by_id.values())
            if len(matches) > 1:
                report["ambiguous_database_matches"].append({
                    "offer_id": offer_id,
                    "row_ids": [x.get("id") for x in matches],
                    "fields": matched_fields,
                })
                continue
            if not matches:
                other_by_id = {}
                other_fields = []
                for field in match_fields:
                    for candidate in all_indexes[field].get(key) or []:
                        rid = int(candidate.get("id"))
                        other_by_id[rid] = candidate
                        if field not in other_fields:
                            other_fields.append(field)
                other = list(other_by_id.values())
                if other:
                    report["supplier_conflicts"].append({
                        "offer_id": offer_id,
                        "row_ids": [x.get("id") for x in other],
                        "fields": other_fields,
                    })
                else:
                    report["unmatched_ozon"] += 1
                    if len(report["unmatched_offer_ids"]) < 500:
                        report["unmatched_offer_ids"].append(offer_id)
                continue

            report["matched_rows"] += 1
            row = matches[0]
            primary_field = next(
                (field for field in match_fields if any(int(x.get("id")) == int(row.get("id")) for x in (norden_indexes[field].get(key) or []))),
                None,
            )
            if primary_field:
                report["matched_by_field"][primary_field] += 1
            schema = schema_by_pid.get(pid) or {}
            dc = int(card.get("description_category_id") or info.get("description_category_id") or 0)
            tid = int(card.get("type_id") or info.get("type_id") or 0)
            p = price_row.get("price") or {}
            if schema_create:
                body = {
                    "id": row["id"],
                    "Ozon ID товара": pid,
                    "Ozon Название": s(card.get("name") or info.get("name") or offer_id),
                    "Ozon Бренд": " | ".join(brand_values(card)) or "Norden",
                    "Ozon Архив": pid in archived_ids,
                    "Ozon Категория": cat_paths.get(dc, ""),
                    "Ozon ID категории": dc or None,
                    "Ozon ID типа": tid or None,
                    "Ozon Описание": s(info.get("description") or info.get("description_text") or card.get("description") or card.get("description_text")),
                    "Ozon Штрихкоды": barcodes(info, card),
                    "Ozon Цена": num(p.get("price")),
                    "Ozon Старая цена": num(p.get("old_price")),
                    "Ozon Маркетинговая цена": num(p.get("marketing_seller_price")),
                    "Ozon Остаток": stock_total(stock_by_id.get(pid) or []),
                    "Ozon Статус": status_text(info),
                    "Ozon Видимость": visibility_text(list_row, info),
                    "Ozon Обновлено": updated_at,
                }
            else:
                body = {"id": row["id"]}
                if "Название" in existing_names:
                    body["Название"] = s(card.get("name") or info.get("name") or offer_id)
                if "Категория" in existing_names and cat_paths.get(dc):
                    body["Категория"] = cat_paths.get(dc)
                if "Цена Ozon" in existing_names and num(p.get("price")) is not None:
                    body["Цена Ozon"] = num(p.get("price"))

            for item in char_rows_by_pid.get(pid) or []:
                field_name = char_field_map.get((int(item["id"]), norm(item["name"])))
                if field_name:
                    body[field_name] = item["value"]
                    report["characteristic_values_written"] += 1

            if schema_create:
                raw_no_images = {
                    "product_id": pid,
                    "offer_id": offer_id,
                    "archived": pid in archived_ids,
                    "product_list": strip_image_keys(list_row),
                    "attributes": strip_image_attributes(card, schema),
                    "info": strip_image_keys(info),
                    "price": strip_image_keys(price_row),
                    "stocks": strip_image_keys(stock_by_id.get(pid) or []),
                }
                body["Ozon данные без изображений (JSON)"] = json.dumps(raw_no_images, ensure_ascii=False, separators=(",", ":"))

            forbidden = [k for k in body if IMAGE_KEY_RE.search(k) or k in {"Первое изображение", "Первое изображение URL", "Все изображения"}]
            if forbidden:
                raise RuntimeError(f"Forbidden image fields in update payload: {forbidden}")

            updates.append(body)
            if idx % 50 == 0:
                write_report(report)

        if updates:
            br.batch_update_rows(CATALOG_TABLE_ID, updates)
        report["updated_rows"] = len(updates)
        report["finished_at"] = now_iso()
        write_report(report)
        print(json.dumps({
            "ozon_norden_total": report["ozon_norden_total"],
            "database_norden_rows": report["database_norden_rows"],
            "matched_rows": report["matched_rows"],
            "updated_rows": report["updated_rows"],
            "unmatched_ozon": report["unmatched_ozon"],
            "characteristic_fields_total": report["characteristic_fields_total"],
            "characteristic_values_written": report["characteristic_values_written"],
            "images_written": report["images_written"],
            "errors": len(report["errors"]),
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        report["errors"].append({"stage": "fatal", "error": str(exc)[:3000]})
        report["finished_at"] = now_iso()
        write_report(report)
        print(json.dumps({"fatal": str(exc)}, ensure_ascii=False))
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
