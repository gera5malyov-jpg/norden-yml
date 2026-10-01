#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "webasyst"))

from client import WebasystClient

BASEROW_URL = os.environ.get("BASEROW_URL", "http://147.78.67.6").rstrip("/")
BASEROW_TOKEN = os.environ.get("BASEROW_DATABASE_TOKEN", "").strip()
WEBASYST_BASE_URL = os.environ.get("WEBASYST_BASE_URL", "https://profikompany.ru").rstrip("/")
WEBASYST_TOKEN = os.environ.get("WEBASYST_API_TOKEN", "").strip()
KIT_TOKEN = os.environ.get("YANDEX_KIT_TOKEN", "").strip()

CATALOG_TABLE_ID = 156
SUPPLIERS_TABLE_ID = 157
SUPPLIER_NAME = "Norden"
WEBASYST_TYPE = "NORDEN-100"
WEBASYST_MAIN_STOCK = "Основной склад"
KIT_TARGET_STOCKS = ("МСК", "СПБ привозной")
FIELD_STOCK = "Остаток Norden"
FIELD_KIT_ID = "Артикул KIT"
REPORT_PATH = HERE / "norden_stock_publish_report.json"
KIT_MAPPING_PATH = ROOT / "norden-kit" / "kit_mapping.json"
KIT_LIVE_INDEX_PATH = HERE / "kit_live_index.json"


def s(v):
    return str(v or "").strip()


def norm(v):
    return unicodedata.normalize("NFKC", s(v)).casefold()


def as_int(v):
    try:
        return max(0, int(float(str(v or 0).replace(",", "."))))
    except Exception:
        return 0


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def listify(payload, keys=()):
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            return [x for x in value.values() if isinstance(x, dict)]
    if payload and all(isinstance(v, dict) for v in payload.values()):
        return list(payload.values())
    return []


def product_skus(product):
    rows = product.get("skus")
    if isinstance(rows, dict):
        return [x for x in rows.values() if isinstance(x, dict)]
    if isinstance(rows, list):
        return [x for x in rows if isinstance(x, dict)]
    return []


class Baserow:
    def __init__(self):
        if not BASEROW_TOKEN:
            raise RuntimeError("BASEROW_DATABASE_TOKEN is missing")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token {BASEROW_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def request(self, method, path):
        r = self.session.request(method, BASEROW_URL + path, timeout=90)
        if not r.ok:
            raise RuntimeError(f"Baserow {method} {path}: HTTP {r.status_code}: {r.text[:1200]}")
        return r.json() if r.content else {}

    def rows(self, table_id):
        out = []
        page = 1
        while True:
            d = self.request(
                "GET",
                f"/api/database/rows/table/{table_id}/?user_field_names=true&size=200&page={page}",
            )
            out.extend(d.get("results") or [])
            if not d.get("next"):
                break
            page += 1
        return out


def load_kit_module():
    path = ROOT / "norden-kit" / "sync_norden_kit.py"
    spec = importlib.util.spec_from_file_location("norden_kit_stock_module", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def wa_stock_qty(sku_row, stock_id):
    stock = sku_row.get("stock")
    if isinstance(stock, dict):
        if stock_id in stock:
            return as_int(stock.get(stock_id))
        if str(stock_id) in stock:
            return as_int(stock.get(str(stock_id)))
    if isinstance(stock, list):
        for row in stock:
            if isinstance(row, dict) and s(row.get("stock_id") or row.get("id")) == s(stock_id):
                return as_int(row.get("count") if "count" in row else row.get("quantity"))
    return None


def supplier_ids(row):
    out = set()
    for x in row.get("Поставщик") or []:
        if isinstance(x, dict) and x.get("id") is not None:
            try:
                out.add(int(x["id"]))
            except Exception:
                pass
    return out


def load_kit_id_index():
    by_kit_id = defaultdict(list)
    if KIT_LIVE_INDEX_PATH.exists():
        payload = json.loads(KIT_LIVE_INDEX_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            for raw_kit_id, rows in payload.items():
                try:
                    kit_id = int(str(raw_kit_id).strip())
                except Exception:
                    continue
                for row in rows or []:
                    if not isinstance(row, dict):
                        continue
                    variant_id = s(row.get("variant_id"))
                    if variant_id:
                        by_kit_id[kit_id].append({
                            "variant_id": variant_id,
                            "supplier_article": "",
                            "sku": s(row.get("sku")),
                        })
            if by_kit_id:
                return by_kit_id

    if not KIT_MAPPING_PATH.exists():
        raise RuntimeError(f"KIT mapping is missing: {KIT_MAPPING_PATH}")
    payload = json.loads(KIT_MAPPING_PATH.read_text(encoding="utf-8"))
    variants = payload.get("variants") if isinstance(payload, dict) else None
    if not isinstance(variants, dict):
        raise RuntimeError("KIT mapping has invalid format")

    for supplier_article, rows in variants.items():
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            kit_id = row.get("kit_id")
            variant_id = s(row.get("variant_id"))
            try:
                kit_id = int(str(kit_id).strip())
            except Exception:
                continue
            if kit_id > 0 and variant_id:
                by_kit_id[kit_id].append({
                    "variant_id": variant_id,
                    "supplier_article": s(supplier_article),
                    "sku": s(row.get("sku")),
                })
    return by_kit_id


def main():
    report = {
        "started_at": now_iso(),
        "source": "Baserow Database",
        "webasyst": {
            "type": WEBASYST_TYPE,
            "main_stock": WEBASYST_MAIN_STOCK,
            "matched": 0,
            "updated": 0,
            "unchanged": 0,
            "unmatched": 0,
            "ambiguous": 0,
            "other_stocks_zeroed": 0,
            "errors": [],
        },
        "kit": {
            "warehouses": list(KIT_TARGET_STOCKS),
            "rows_with_kit_id": 0,
            "mapped": 0,
            "stock_updates": 0,
            "missing_mapping": 0,
            "ambiguous_mapping": 0,
            "errors": [],
        },
    }

    br = Baserow()
    supplier_rows = br.rows(SUPPLIERS_TABLE_ID)
    norden_suppliers = [r for r in supplier_rows if norm(r.get("Поставщик")) == norm(SUPPLIER_NAME)]
    if len(norden_suppliers) != 1:
        raise RuntimeError(f"Expected one Norden supplier row, found {len(norden_suppliers)}")
    supplier_id = int(norden_suppliers[0]["id"])

    all_rows = br.rows(CATALOG_TABLE_ID)
    norden_rows = [
        row for row in all_rows
        if supplier_id in supplier_ids(row) or norm(row.get("Бренд")) == norm(SUPPLIER_NAME)
    ]

    # Database is the only stock source for downstream systems.
    by_wa_sku = defaultdict(list)
    for row in norden_rows:
        sku = s(row.get("Артикул"))
        if sku:
            by_wa_sku[sku].append(row)

    # --- Webasyst ---
    if not WEBASYST_TOKEN:
        raise RuntimeError("WEBASYST_API_TOKEN is missing")
    wa = WebasystClient(
        base_url=WEBASYST_BASE_URL,
        token=WEBASYST_TOKEN,
        min_request_interval=0.35,
    )

    types = listify(wa.call("shop.type.getList"))
    type_matches = [
        x for x in types
        if norm(x.get("name") or x.get("title")) == norm(WEBASYST_TYPE)
    ]
    if len(type_matches) != 1:
        raise RuntimeError(f"Expected one Webasyst type {WEBASYST_TYPE!r}, found {len(type_matches)}")
    type_id = s(type_matches[0].get("id"))

    wa_stocks = listify(wa.call("shop.stock.getList"))
    stock_id_by_name = {
        s(x.get("name") or x.get("title")): s(x.get("id"))
        for x in wa_stocks
        if s(x.get("id"))
    }
    main_stock_ids = [
        sid for name, sid in stock_id_by_name.items()
        if norm(name) == norm(WEBASYST_MAIN_STOCK)
    ]
    if len(main_stock_ids) != 1:
        raise RuntimeError(
            f"Expected one Webasyst stock {WEBASYST_MAIN_STOCK!r}, found {len(main_stock_ids)}"
        )
    main_stock_id = main_stock_ids[0]
    all_stock_ids = list(dict.fromkeys(stock_id_by_name.values()))

    products = []
    offset = 0
    while True:
        payload = wa.call(
            "shop.product.search",
            params={
                "hash": f"type/{type_id}",
                "offset": offset,
                "limit": 1000,
                "fields": "*,skus,stock_counts",
            },
        )
        batch = listify(payload, ("products", "items"))
        products.extend(batch)
        if not batch or len(batch) < 1000:
            break
        offset += len(batch)

    for product in products:
        for sku_row in product_skus(product):
            sku = s(sku_row.get("sku"))
            if not sku:
                continue
            matches = by_wa_sku.get(sku, [])
            if len(matches) == 0:
                report["webasyst"]["unmatched"] += 1
                # Still enforce the user's rule: all non-main Norden warehouses
                # are zero. Main warehouse is left untouched if the DB row cannot
                # be resolved safely.
                body = {}
                for stock_id in all_stock_ids:
                    if stock_id == main_stock_id:
                        continue
                    current = wa_stock_qty(sku_row, stock_id)
                    if current is None or current != 0:
                        body[stock_id] = "0"
                if body:
                    wa.call(
                        "shop.product.skus.update",
                        http_method="POST",
                        params={"id": s(sku_row.get("id"))},
                        data={"stock": body},
                    )
                    report["webasyst"]["other_stocks_zeroed"] += len(body)
                continue
            if len(matches) > 1:
                report["webasyst"]["ambiguous"] += 1
                continue

            report["webasyst"]["matched"] += 1
            qty = as_int(matches[0].get(FIELD_STOCK))

            desired = {
                stock_id: str(qty if stock_id == main_stock_id else 0)
                for stock_id in all_stock_ids
            }
            changed = False
            other_zeroed = 0
            for stock_id, value in desired.items():
                current = wa_stock_qty(sku_row, stock_id)
                target = as_int(value)
                if current is None or current != target:
                    changed = True
                    if stock_id != main_stock_id and target == 0:
                        other_zeroed += 1

            if changed:
                wa.call(
                    "shop.product.skus.update",
                    http_method="POST",
                    params={"id": s(sku_row.get("id"))},
                    data={"stock": desired},
                )
                report["webasyst"]["updated"] += 1
                report["webasyst"]["other_stocks_zeroed"] += other_zeroed
            else:
                report["webasyst"]["unchanged"] += 1

    # --- KIT ---
    mod = load_kit_module()
    kit = mod.KitClient(KIT_TOKEN)
    warehouses = mod.resolve_warehouses(kit)
    target_warehouse_ids = {
        name: warehouses[name] for name in KIT_TARGET_STOCKS
    }
    kit_index = load_kit_id_index()

    stock_rows = []
    for row in norden_rows:
        raw_kit_id = row.get(FIELD_KIT_ID)
        try:
            kit_id = int(float(raw_kit_id))
        except Exception:
            continue
        if kit_id <= 0:
            continue

        report["kit"]["rows_with_kit_id"] += 1
        matches = kit_index.get(kit_id, [])
        unique_variant_ids = list(dict.fromkeys(s(x.get("variant_id")) for x in matches if s(x.get("variant_id"))))
        if len(unique_variant_ids) == 0:
            report["kit"]["missing_mapping"] += 1
            continue
        if len(unique_variant_ids) > 1:
            report["kit"]["ambiguous_mapping"] += 1
            continue

        report["kit"]["mapped"] += 1
        variant_id = unique_variant_ids[0]
        qty = as_int(row.get(FIELD_STOCK))
        for warehouse_id in target_warehouse_ids.values():
            stock_rows.append({
                "variant_id": variant_id,
                "warehouse_id": warehouse_id,
                "quantity": qty,
            })

    if stock_rows:
        skipped = kit.bulk_stocks(stock_rows)
        report["kit"]["stock_updates"] = len(stock_rows) - len(skipped)
        if skipped:
            report["kit"]["errors"].append({
                "stage": "bulk_stocks",
                "skipped_variant_ids": skipped[:200],
                "skipped_count": len(skipped),
            })

    report["finished_at"] = now_iso()
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if report["webasyst"]["ambiguous"] or report["kit"]["ambiguous_mapping"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
