#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import gspread
import requests
from google.oauth2.service_account import Credentials

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webasyst"))
from client import WebasystClient

TYPE_NAME = "NORDEN-100"
SHEET_NAME = "Норден"
SPREADSHEET_ID = os.environ["CATALOG_SPREADSHEET_ID"].strip()
SA_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
PRICE_XML_URL = "https://norden.group/index.php?dispatch=sw_user_prices.get_file&file=Norden.group+-K8%25.xml"
REPORT = ROOT / "catalog" / "webasyst_norden_yml_id_update_report.json"

def s(v):
    return str(v or "").strip()

def norm(v):
    return re.sub(r"[^0-9a-zа-яё]+", "", unicodedata.normalize("NFKC", s(v)).casefold())

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def listify(payload, keys=()):
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys:
        v = payload.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
        if isinstance(v, dict):
            return [x for x in v.values() if isinstance(x, dict)]
    if payload and all(isinstance(v, dict) for v in payload.values()):
        return list(payload.values())
    return []

def product_skus(product):
    v = product.get("skus")
    if isinstance(v, dict):
        return [x for x in v.values() if isinstance(x, dict)]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    return []

def num(v):
    raw = s(v).replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not raw:
        return 0.0
    try:
        return float(raw)
    except Exception:
        return 0.0

# 1) Read current table: exact SKU/article -> YML ID.
creds = json.loads(SA_JSON)
gc = gspread.authorize(Credentials.from_service_account_info(
    creds,
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ],
))
ws = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
values = ws.get_all_values()
headers = values[0]
article_idx = headers.index("Артикул")
yml_idx = headers.index("YML ID")

desired = {}
table_conflicts = {}
for row in values[1:]:
    article = s(row[article_idx] if article_idx < len(row) else "")
    yml_id = s(row[yml_idx] if yml_idx < len(row) else "")
    if not article or not yml_id:
        continue
    k = norm(article)
    if k in desired and desired[k] != yml_id:
        table_conflicts.setdefault(article, set()).update([desired[k], yml_id])
    desired[k] = yml_id

if table_conflicts:
    raise RuntimeError("Safety stop: conflicting YML ID values for duplicate articles in table")

# 2) Load only Webasyst type NORDEN-100.
wa = WebasystClient(min_request_interval=0.55)
types = listify(wa.call("shop.type.getList"))
type_matches = [x for x in types if norm(x.get("name") or x.get("title")) == norm(TYPE_NAME)]
if len(type_matches) != 1:
    raise RuntimeError(f"Expected exactly one Webasyst type {TYPE_NAME}, found {len(type_matches)}")
type_id = s(type_matches[0].get("id"))

products = []
offset = 0
while True:
    payload = wa.call(
        "shop.product.search",
        params={
            "hash": f"type/{type_id}",
            "offset": offset,
            "limit": 1000,
            "fields": "*,skus",
        },
    )
    batch = listify(payload, ("products", "items"))
    products.extend(batch)
    total = (payload.get("count") or payload.get("total_count")) if isinstance(payload, dict) else None
    if not batch or len(batch) < 1000:
        break
    if total not in (None, "") and len(products) >= int(total):
        break
    offset += len(batch)

# Exact SKU -> product mapping; duplicates are skipped for safety.
sku_map = {}
sku_duplicates = {}
for product in products:
    pid = s(product.get("id"))
    for sku_row in product_skus(product):
        sku = s(sku_row.get("sku"))
        if not sku:
            continue
        k = norm(sku)
        if k in sku_map and s(sku_map[k].get("id")) != pid:
            sku_duplicates.setdefault(sku, set()).update([s(sku_map[k].get("id")), pid])
        else:
            sku_map[k] = product

report = {
    "started_at": now_iso(),
    "webasyst_type": TYPE_NAME,
    "webasyst_type_id": type_id,
    "table_pairs": len(desired),
    "webasyst_products": len(products),
    "webasyst_duplicate_skus": {k: sorted(v) for k, v in sku_duplicates.items()},
    "matched_by_exact_article": 0,
    "updated_yml_id": 0,
    "already_equal": 0,
    "not_found_in_webasyst": 0,
    "skipped_duplicate_sku": 0,
    "errors": [],
    "sample_updates": [],
}

# 3) Update ONLY product.yml_id.
for article_key, desired_yml in desired.items():
    if any(norm(k) == article_key for k in sku_duplicates):
        report["skipped_duplicate_sku"] += 1
        continue
    product = sku_map.get(article_key)
    if product is None:
        report["not_found_in_webasyst"] += 1
        continue

    report["matched_by_exact_article"] += 1
    current_yml = s(product.get("yml_id"))
    if current_yml == desired_yml:
        report["already_equal"] += 1
        continue

    try:
        pid = s(product.get("id"))
        wa.call(
            "shop.product.update",
            http_method="POST",
            params={"id": pid},
            data={"yml_id": desired_yml},
        )
        report["updated_yml_id"] += 1
        if len(report["sample_updates"]) < 100:
            report["sample_updates"].append({
                "product_id": pid,
                "article": next((s(x.get("sku")) for x in product_skus(product) if norm(x.get("sku")) == article_key), ""),
                "old_yml_id": current_yml,
                "new_yml_id": desired_yml,
            })
    except Exception as exc:
        report["errors"].append({
            "article_key": article_key,
            "message": str(exc)[:1000],
        })

# 4) Re-read Webasyst to verify the written YML IDs.
time.sleep(1.0)
verified_products = []
offset = 0
while True:
    payload = wa.call(
        "shop.product.search",
        params={
            "hash": f"type/{type_id}",
            "offset": offset,
            "limit": 1000,
            "fields": "*,skus",
        },
    )
    batch = listify(payload, ("products", "items"))
    verified_products.extend(batch)
    total = (payload.get("count") or payload.get("total_count")) if isinstance(payload, dict) else None
    if not batch or len(batch) < 1000:
        break
    if total not in (None, "") and len(verified_products) >= int(total):
        break
    offset += len(batch)

verified_by_article = {}
for product in verified_products:
    for sku_row in product_skus(product):
        sku = s(sku_row.get("sku"))
        if sku:
            verified_by_article[norm(sku)] = s(product.get("yml_id"))

verify_mismatches = []
for k, desired_yml in desired.items():
    if k in verified_by_article and verified_by_article[k] != desired_yml:
        verify_mismatches.append({
            "article_key": k,
            "expected_yml_id": desired_yml,
            "actual_yml_id": verified_by_article[k],
        })
report["verification_mismatches"] = verify_mismatches

# 5) Re-check supplier file: in-stock chairs absent from user's current table.
catalog_yml_ids = {norm(v) for v in desired.values() if s(v)}
catalog_articles = set(desired.keys())

resp = requests.get(PRICE_XML_URL, timeout=180, headers={"User-Agent": "Mozilla/5.0"})
resp.raise_for_status()
root = ET.fromstring(resp.content)

missing_chairs = []
for node in root.iter("Номенклатура"):
    article = s(node.findtext("Артикул"))
    name = s(node.findtext("НаименованиеПолное") or node.findtext("Наименование"))
    if not article or "кресл" not in name.casefold():
        continue
    stock = sum(num(st.text) for st in node.findall("СвободныйОстаток"))
    if stock <= 0:
        continue
    k = norm(article)
    if k in catalog_yml_ids or k in catalog_articles:
        continue
    prices = {s(p.attrib.get("ВидЦен")): num(p.text) for p in node.findall("Цена")}
    missing_chairs.append({
        "yml_id": article,
        "name": name,
        "stock": int(stock) if float(stock).is_integer() else stock,
        "opt": prices.get("Опт", 0),
        "rrp": prices.get("РРЦ", 0),
    })

missing_chairs.sort(key=lambda x: (-x["stock"], x["name"]))
report["missing_instock_chairs_count"] = len(missing_chairs)
report["missing_instock_chairs"] = missing_chairs
report["finished_at"] = now_iso()
report["complete"] = not report["errors"] and not verify_mismatches

REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
raise SystemExit(0 if report["complete"] else 2)
