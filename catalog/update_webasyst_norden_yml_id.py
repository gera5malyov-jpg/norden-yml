#!/usr/bin/env python3
from __future__ import annotations
import json, os, re, sys, time, unicodedata
from pathlib import Path
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webasyst"))
from client import WebasystClient

TYPE_NAME = "NORDEN-100"
SPREADSHEET_ID = os.environ["CATALOG_SPREADSHEET_ID"].strip()
SHEET_NAME = os.environ.get("CATALOG_SHEET", "Норден").strip()
SA_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
REPORT = ROOT / "catalog" / "webasyst_norden_yml_id_update_report.json"

def s(v): return str(v or "").strip()
def norm(v): return re.sub(r"[^0-9a-zа-яё]+","",unicodedata.normalize("NFKC",s(v)).casefold())
def now(): return datetime.now(timezone.utc).isoformat()

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

def skus(product):
    v = product.get("skus")
    if isinstance(v, dict):
        return [x for x in v.values() if isinstance(x, dict)]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    return []

# Read table: SKU/article -> YML ID.
creds = json.loads(SA_JSON)
gc = gspread.authorize(Credentials.from_service_account_info(
    creds,
    scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
))
ws = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
values = ws.get_all_values()
if not values:
    raise RuntimeError("Catalog sheet is empty")
headers = values[0]
ai = headers.index("Артикул")
yi = headers.index("YML ID")

desired = {}
conflicts = {}
for row in values[1:]:
    article = s(row[ai] if ai < len(row) else "")
    yml_id = s(row[yi] if yi < len(row) else "")
    if not article or not yml_id:
        continue
    k = norm(article)
    if k in desired and desired[k] != yml_id:
        conflicts.setdefault(article, set()).update([desired[k], yml_id])
    desired[k] = yml_id

if conflicts:
    raise RuntimeError("Safety stop: duplicate catalog SKU with different YML ID values")

wa = WebasystClient(min_request_interval=0.50)
types = listify(wa.call("shop.type.getList"))
type_matches = [x for x in types if norm(x.get("name") or x.get("title")) == norm(TYPE_NAME)]
if len(type_matches) != 1:
    raise RuntimeError(f"Expected exactly one Webasyst type {TYPE_NAME}, found {len(type_matches)}")
type_id = s(type_matches[0].get("id"))

# Read all existing products in NORDEN-100.
products = []
offset = 0
while True:
    payload = wa.call("shop.product.search", params={
        "hash": f"type/{type_id}",
        "offset": offset,
        "limit": 1000,
        "fields": "*,skus",
    })
    batch = listify(payload, ("products","items"))
    products.extend(batch)
    total = (payload.get("count") or payload.get("total_count")) if isinstance(payload, dict) else None
    if not batch or len(batch) < 1000:
        break
    if total not in (None,"") and len(products) >= int(total):
        break
    offset += len(batch)

# Exact SKU index, no fuzzy matching.
wa_by_sku = {}
duplicate_wa = {}
for product in products:
    for sku_row in skus(product):
        sku = s(sku_row.get("sku"))
        if not sku:
            continue
        k = norm(sku)
        entry = {"product": product, "sku": sku_row}
        if k in wa_by_sku:
            duplicate_wa.setdefault(sku, []).append(s(product.get("id")))
        else:
            wa_by_sku[k] = entry

report = {
    "started_at": now(),
    "policy": {
        "match": "exact normalized article/SKU only",
        "allowed_webasyst_write": ["yml_id"],
        "creates": False,
        "prices": False,
        "stocks": False,
        "names": False,
        "features": False,
        "type": False,
        "status": False,
        "skus": False,
    },
    "catalog_pairs": len(desired),
    "webasyst_type": TYPE_NAME,
    "webasyst_type_id": type_id,
    "webasyst_products": len(products),
    "webasyst_duplicate_skus": duplicate_wa,
    "matched": 0,
    "updated": 0,
    "unchanged": 0,
    "missing_in_webasyst": 0,
    "errors": [],
    "sample_updates": [],
    "sample_missing": [],
    "verification_mismatches": [],
}

if duplicate_wa:
    raise RuntimeError(f"Safety stop: duplicate Webasyst SKUs found: {len(duplicate_wa)}")

# Update only yml_id on matched products.
updated_product_ids = []
for article_key, yml_id in desired.items():
    entry = wa_by_sku.get(article_key)
    if not entry:
        report["missing_in_webasyst"] += 1
        if len(report["sample_missing"]) < 100:
            report["sample_missing"].append({"article_key": article_key, "yml_id": yml_id})
        continue

    product = entry["product"]
    report["matched"] += 1
    product_id = s(product.get("id"))
    current = s(product.get("yml_id"))

    if current == yml_id:
        report["unchanged"] += 1
        continue

    try:
        # IMPORTANT: one field only.
        wa.call(
            "shop.product.update",
            http_method="POST",
            params={"id": product_id},
            data={"yml_id": yml_id},
        )
        report["updated"] += 1
        updated_product_ids.append((product_id, yml_id, s(entry["sku"].get("sku"))))
        if len(report["sample_updates"]) < 100:
            report["sample_updates"].append({
                "product_id": product_id,
                "sku": s(entry["sku"].get("sku")),
                "old_yml_id": current,
                "new_yml_id": yml_id,
            })
    except Exception as exc:
        report["errors"].append({
            "product_id": product_id,
            "sku": s(entry["sku"].get("sku")),
            "message": str(exc)[:1200],
        })

# Verification: reread every changed product and compare yml_id.
for product_id, wanted, sku in updated_product_ids:
    try:
        info = wa.call("shop.product.getInfo", params={"id": product_id})
        got = s(info.get("yml_id") if isinstance(info, dict) else "")
        if got != wanted:
            report["verification_mismatches"].append({
                "product_id": product_id,
                "sku": sku,
                "expected": wanted,
                "actual": got,
            })
    except Exception as exc:
        report["verification_mismatches"].append({
            "product_id": product_id,
            "sku": sku,
            "expected": wanted,
            "actual": f"VERIFY_ERROR: {str(exc)[:600]}",
        })

report["finished_at"] = now()
report["complete"] = not report["errors"] and not report["verification_mismatches"]
REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
raise SystemExit(0 if report["complete"] else 2)
