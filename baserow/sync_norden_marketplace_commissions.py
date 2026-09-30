#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

BASEROW_URL = os.environ.get("BASEROW_URL", "http://147.78.67.6").rstrip("/")
BASEROW_TOKEN = os.environ.get("BASEROW_DATABASE_TOKEN", "").strip()
OZON_CLIENT_ID = os.environ.get("OZON_CLIENT_ID", "").strip()
OZON_API_KEY = os.environ.get("OZON_API_KEY", "").strip()
YANDEX_API_KEY = os.environ.get("YANDEX_MARKET_API_KEY", "").strip()
YANDEX_BUSINESS_ID = str(os.environ.get("YANDEX_MARKET_BUSINESS_ID") or "20806099").strip()
YANDEX_CAMPAIGN_ID = str(os.environ.get("YANDEX_MARKET_CAMPAIGN_ID") or "89405839").strip()

CATALOG_TABLE_ID = 156
SUPPLIERS_TABLE_ID = 157
SUPPLIER_NAME = "Norden"
FIELD_OZON = "Комиссия Ozon"
FIELD_YANDEX = "Комиссия Яндекс"
FIELD_OZON_CATEGORY = "Категория Ozon"
FIELD_YANDEX_CATEGORY = "Категория Яндекс"
REPORT_PATH = Path("baserow/norden_marketplace_commissions_report.json")

OZON = "https://api-seller.ozon.ru"
YANDEX = "https://api.partner.market.yandex.ru"


def s(v):
    return str(v or "").strip()


def norm(v):
    return unicodedata.normalize("NFKC", s(v)).casefold()


def num(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def pct(amount, price):
    return (amount / price * 100.0) if amount > 0 and price > 0 else 0.0


def chunks(rows, size):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def api_call(session, method, url, *, body=None, params=None, retries=8):
    last = None
    for attempt in range(retries):
        try:
            r = session.request(method, url, json=body, params=params, timeout=90)
        except requests.RequestException as exc:
            last = exc
            time.sleep(min(20, 2 ** attempt))
            continue
        last = r
        if r.status_code in (420, 429) or r.status_code >= 500:
            time.sleep(float(r.headers.get("Retry-After") or min(45, 2 ** attempt)))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} {method} {url}: {r.text[:1200]}")
        return r.json() if r.content else {}
    raise RuntimeError(f"Request failed {method} {url}: {last}")


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

    def request(self, method, path, **kwargs):
        return api_call(self.session, method, BASEROW_URL + path, **kwargs)

    def fields(self, table_id):
        return self.request("GET", f"/api/database/fields/table/{table_id}/") or []

    def ensure_number_field(self, table_id, name):
        fields = self.fields(table_id)
        found = [x for x in fields if s(x.get("name")) == name]
        if found:
            if found[0].get("type") != "number":
                raise RuntimeError(f"Field {name!r} exists but is not numeric")
            return
        body = {"name": name, "type": "number", "number_decimal_places": 2, "number_negative": False}
        self.request("POST", f"/api/database/fields/table/{table_id}/", body=body)

    def ensure_text_field(self, table_id, name):
        fields = self.fields(table_id)
        found = [x for x in fields if s(x.get("name")) == name]
        if found:
            if found[0].get("type") not in ("text", "long_text"):
                raise RuntimeError(f"Field {name!r} exists but is not text")
            return
        self.request(
            "POST",
            f"/api/database/fields/table/{table_id}/",
            body={"name": name, "type": "text"},
        )

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

    def batch_update(self, table_id, items):
        for batch in chunks(items, 100):
            self.request(
                "PATCH",
                f"/api/database/rows/table/{table_id}/batch/?user_field_names=true",
                body={"items": batch},
            )


def ozon_commissions(offer_ids):
    if not OZON_CLIENT_ID or not OZON_API_KEY:
        raise RuntimeError("OZON credentials are missing")
    session = requests.Session()
    session.headers.update({
        "Client-Id": OZON_CLIENT_ID,
        "Api-Key": OZON_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })

    # Category tree gives the marketplace category name/path for each
    # description_category_id returned by product attributes.
    category_paths = {}
    tree = api_call(
        session,
        "POST",
        OZON + "/v1/description-category/tree",
        body={"language": "DEFAULT"},
    )

    def walk(nodes, prefix):
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            name = s(node.get("category_name"))
            path = prefix + ([name] if name else [])
            cid = node.get("description_category_id")
            if cid not in (None, ""):
                try:
                    category_paths[int(cid)] = " > ".join(path)
                except Exception:
                    pass
            walk(node.get("children") or [], path)

    walk(tree.get("result") or [], [])

    result = {}
    product_ids = []
    offer_by_pid = {}

    for batch in chunks(offer_ids, 100):
        d = api_call(
            session,
            "POST",
            OZON + "/v5/product/info/prices",
            body={"cursor": "", "filter": {"offer_id": batch, "visibility": "ALL"}, "limit": 100},
        )
        for x in d.get("items") or []:
            offer = s(x.get("offer_id"))
            if not offer:
                continue
            commissions = x.get("commissions") or {}
            price = x.get("price") or {}
            sale_price = num(price.get("marketing_seller_price") or price.get("price"))
            sales_rfbs = num(commissions.get("sales_percent_rfbs"))
            acquiring_amount = num(x.get("acquiring"))
            acquiring_percent = pct(acquiring_amount, sale_price)
            pid = x.get("product_id")
            if pid not in (None, ""):
                try:
                    pid = int(pid)
                    product_ids.append(pid)
                    offer_by_pid[pid] = offer
                except Exception:
                    pass
            result[offer] = {
                "sales_percent": sales_rfbs,
                "acquiring_percent": acquiring_percent,
                "total_percent": round(sales_rfbs + acquiring_percent, 2),
                "category": "",
            }

    # Product attributes contain description_category_id. Resolve it to a
    # readable Ozon category path and store it next to the commission.
    for batch in chunks(sorted(set(product_ids)), 1000):
        d = api_call(
            session,
            "POST",
            OZON + "/v4/product/info/attributes",
            body={"filter": {"product_id": batch, "visibility": "ALL"}, "limit": 1000},
        )
        for x in d.get("result") or []:
            try:
                pid = int(x.get("id") or x.get("product_id") or 0)
            except Exception:
                pid = 0
            offer = offer_by_pid.get(pid) or s(x.get("offer_id"))
            if not offer or offer not in result:
                continue
            try:
                cid = int(x.get("description_category_id") or 0)
            except Exception:
                cid = 0
            if cid:
                result[offer]["category"] = category_paths.get(cid, "") or str(cid)

    return result


def yandex_offer_data(offer_ids):
    if not YANDEX_API_KEY:
        raise RuntimeError("YANDEX_MARKET_API_KEY is missing")
    session = requests.Session()
    session.headers.update({
        "Api-Key": YANDEX_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })

    offers = {}
    for batch in chunks(offer_ids, 200):
        d = api_call(
            session,
            "POST",
            f"{YANDEX}/v2/campaigns/{YANDEX_CAMPAIGN_ID}/offers",
            body={"offerIds": batch},
        )
        for x in ((d.get("result") or {}).get("offers") or []):
            oid = s(x.get("offerId"))
            if oid:
                offers[oid] = x

    mappings = {}
    for batch in chunks(offer_ids, 100):
        d = api_call(
            session,
            "POST",
            f"{YANDEX}/v2/businesses/{YANDEX_BUSINESS_ID}/offer-mappings",
            body={"offerIds": batch},
        )
        for x in ((d.get("result") or {}).get("offerMappings") or []):
            off = dict(x.get("offer") or {})
            mp = x.get("mapping") or {}
            if not off.get("marketCategoryId") and mp.get("marketCategoryId"):
                off["marketCategoryId"] = mp.get("marketCategoryId")
            oid = s(off.get("offerId"))
            if oid:
                mappings[oid] = off

    calc_inputs = []
    for oid, off in offers.items():
        mp = mappings.get(oid) or {}
        wd = mp.get("weightDimensions") or {}
        price = num((off.get("campaignPrice") or {}).get("value") or (off.get("basicPrice") or {}).get("value"))
        category = int(mp.get("marketCategoryId") or 0)
        length, width, height, weight = map(num, (wd.get("length"), wd.get("width"), wd.get("height"), wd.get("weight")))
        if category > 0 and price > 0 and min(length, width, height, weight) > 0:
            calc_inputs.append((oid, {
                "categoryId": category,
                "price": price,
                "length": length,
                "width": width,
                "height": height,
                "weight": weight,
                "quantity": 1,
            }))

    result = {}
    for batch in chunks(calc_inputs, 200):
        ids = [x[0] for x in batch]
        req = [x[1] for x in batch]
        d = api_call(
            session,
            "POST",
            YANDEX + "/v2/tariffs/calculate",
            body={"parameters": {"campaignId": int(YANDEX_CAMPAIGN_ID)}, "offers": req},
        )
        rows = ((d.get("result") or {}).get("offers") or [])
        if len(rows) != len(ids):
            raise RuntimeError(f"Yandex tariff response mismatch: {len(rows)} != {len(ids)}")
        for oid, tr in zip(ids, rows):
            services = tr.get("tariffs") or []
            fee = sum(num(x.get("amount")) for x in services if s(x.get("type")) == "FEE")
            acquiring = sum(
                num(x.get("amount"))
                for x in services
                if s(x.get("type")) in ("AGENCY_COMMISSION", "PAYMENT_TRANSFER")
            )
            off = offers[oid]
            price = num((off.get("campaignPrice") or {}).get("value") or (off.get("basicPrice") or {}).get("value"))
            mp = mappings.get(oid) or {}
            result[oid] = {
                "fee_percent": pct(fee, price),
                "acquiring_percent": pct(acquiring, price),
                "total_percent": round(pct(fee + acquiring, price), 2),
                "category": s(mp.get("marketCategoryName")),
            }
    return result


def main():
    started = datetime.now(timezone.utc).isoformat()
    br = Baserow()
    br.ensure_number_field(CATALOG_TABLE_ID, FIELD_OZON)
    br.ensure_number_field(CATALOG_TABLE_ID, FIELD_YANDEX)
    br.ensure_text_field(CATALOG_TABLE_ID, FIELD_OZON_CATEGORY)
    br.ensure_text_field(CATALOG_TABLE_ID, FIELD_YANDEX_CATEGORY)

    suppliers = br.rows(SUPPLIERS_TABLE_ID)
    norden = [x for x in suppliers if norm(x.get("Поставщик")) == norm(SUPPLIER_NAME)]
    if len(norden) != 1:
        raise RuntimeError(f"Expected one Norden supplier row, found {len(norden)}")
    supplier_id = int(norden[0]["id"])

    def is_norden(row):
        linked = {
            int(x["id"]) for x in (row.get("Поставщик") or [])
            if isinstance(x, dict) and x.get("id") is not None
        }
        return supplier_id in linked or norm(row.get("Бренд")) == norm(SUPPLIER_NAME)

    rows = [x for x in br.rows(CATALOG_TABLE_ID) if is_norden(x)]
    by_offer = defaultdict(list)
    for row in rows:
        offer = s(row.get("Артикул"))
        if not offer:
            alt = s(row.get("Наименование артикула"))
            if alt.upper().startswith("AF-"):
                offer = alt
        if offer:
            by_offer[offer].append(row)

    offer_ids = sorted(by_offer)
    oz = ozon_commissions(offer_ids)
    ya = yandex_offer_data(offer_ids)

    updates = []
    oz_changed = ya_changed = 0
    oz_category_changed = ya_category_changed = 0
    for offer, matched_rows in by_offer.items():
        for row in matched_rows:
            body = {"id": row["id"]}
            changed = False
            if offer in oz:
                value = oz[offer]["total_percent"]
                try:
                    same = round(float(row.get(FIELD_OZON) or 0), 2) == value
                except Exception:
                    same = False
                if not same:
                    body[FIELD_OZON] = value
                    oz_changed += 1
                    changed = True

                category = s(oz[offer].get("category"))
                if category and s(row.get(FIELD_OZON_CATEGORY)) != category:
                    body[FIELD_OZON_CATEGORY] = category
                    oz_category_changed += 1
                    changed = True

            if offer in ya:
                value = ya[offer]["total_percent"]
                try:
                    same = round(float(row.get(FIELD_YANDEX) or 0), 2) == value
                except Exception:
                    same = False
                if not same:
                    body[FIELD_YANDEX] = value
                    ya_changed += 1
                    changed = True

                category = s(ya[offer].get("category"))
                if category and s(row.get(FIELD_YANDEX_CATEGORY)) != category:
                    body[FIELD_YANDEX_CATEGORY] = category
                    ya_category_changed += 1
                    changed = True
            if changed:
                updates.append(body)

    if updates:
        br.batch_update(CATALOG_TABLE_ID, updates)

    report = {
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "scheme": {"ozon": "RFBS", "yandex": "DBS"},
        "baserow_norden_rows": len(rows),
        "offers_checked": len(offer_ids),
        "ozon_found": len(oz),
        "yandex_found": len(ya),
        "ozon_rows_updated": oz_changed,
        "yandex_rows_updated": ya_changed,
        "ozon_category_rows_updated": oz_category_changed,
        "yandex_category_rows_updated": ya_category_changed,
        "rows_updated": len(updates),
        "fields": {
            FIELD_OZON: "sales_percent_rfbs + acquiring_percent",
            FIELD_YANDEX: "DBS FEE + AGENCY_COMMISSION/PAYMENT_TRANSFER",
            FIELD_OZON_CATEGORY: "Ozon description category path",
            FIELD_YANDEX_CATEGORY: "Yandex Market marketCategoryName",
        },
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
