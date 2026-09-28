#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from datetime import timedelta, timezone
from pathlib import Path

import gspread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webasyst"))
sys.path.insert(0, str(ROOT / "orders-sheet"))

import marketplace_order_sync as mp
import sync_orders_sheet as oss
from client import WebasystClient

TARGET_DATES = {"2026-09-26", "2026-09-27"}
TARGET_YANDEX = {"62301041410"}
TARGET_OZON = {"0128881552-0579-1", "29680191-1530-1"}
MSK = timezone(timedelta(hours=3))
TAB = "Восстановление 26-27"

def local_date(value):
    x = mp.dt(value)
    return x.astimezone(MSK).strftime("%Y-%m-%d") if x else ""

def quantity(value):
    try:
        return max(1, int(float(str(value or 1).replace(",", "."))))
    except Exception:
        return 1

wa = WebasystClient(min_request_interval=0.10)
name_cache = {}

def sku_name(code):
    code = oss.s(code)
    if not code:
        return ""
    if code in name_cache:
        return name_cache[code]
    name = code
    try:
        p = wa.call("shop.product.search", params={
            "hash": f"search/query={code}", "limit": 30, "fields": "id,name,skus"
        })
        products = oss.listify((p or {}).get("products") if isinstance(p, dict) else p)
        for prod in products:
            for sku in oss.listify(prod.get("skus")):
                if oss.s(sku.get("sku")) == code:
                    name = oss.s(prod.get("name")) or code
                    raise StopIteration
    except StopIteration:
        pass
    except Exception:
        pass
    name_cache[code] = name
    return name

rows = []
errors = []

def add_row(created, source, order_no, phone, items, qty):
    d = local_date(created)
    if d in TARGET_DATES:
        rows.append([d, source, order_no, phone or "", items or "", qty or 0])

# Yandex order that first appeared in the 26 Sep scheduled sync.
try:
    token = os.getenv("YANDEX_MARKET_API_KEY", "").strip()
    h = {"Api-Key": token, "Accept": "application/json", "Content-Type": "application/json"}
    rr = mp.net.req("GET", "https://api.partner.market.yandex.ru/v2/campaigns", headers=h, params={"limit": 100})
    campaigns = rr.json().get("campaigns") or [] if rr.ok else []
    business_ids = []
    for c in campaigns:
        b = c.get("business") if isinstance(c, dict) else None
        bid = b.get("id") if isinstance(b, dict) else c.get("businessId") if isinstance(c, dict) else None
        if bid and bid not in business_ids:
            business_ids.append(bid)
    done = False
    for bid in business_ids:
        page = None
        while not done:
            params = {"limit": 50}
            if page:
                params["pageToken"] = page
            q = mp.net.req("POST", f"https://api.partner.market.yandex.ru/v1/businesses/{bid}/orders", headers=h, params=params, body={})
            if not q.ok:
                break
            data = q.json()
            root = data.get("result") if isinstance(data.get("result"), dict) else data
            orders = root.get("orders") or []
            for o in orders:
                oid = str(o.get("orderId") or "")
                if oid not in TARGET_YANDEX:
                    continue
                cid = str(o.get("campaignId") or "")
                detail = mp.yandex_order_detail(h, cid, oid) if cid else {}
                buyer = mp.yandex_buyer_info(h, cid, oid, str(o.get("status") or "")) if cid else {}
                if not buyer and isinstance(detail.get("buyer"), dict):
                    buyer = detail.get("buyer") or {}
                phone = oss.phone_text(buyer)
                raw_items = detail.get("items") if isinstance(detail.get("items"), list) else o.get("items") or []
                parts, total = [], 0
                for it in raw_items:
                    if not isinstance(it, dict):
                        continue
                    n = quantity(it.get("count") or it.get("quantity"))
                    total += n
                    sku = oss.s(it.get("offerId") or it.get("shopSku"))
                    name = oss.s(it.get("offerName") or it.get("name")) or sku_name(sku)
                    parts.append(f"{name or 'Товар'} × {n}")
                add_row(o.get("creationDate"), "Яндекс Маркет", oid, phone, "; ".join(parts), total)
                done = True
                break
            paging = root.get("paging") if isinstance(root, dict) else None
            page = (paging or {}).get("nextPageToken") if isinstance(paging, dict) else None
            if not page:
                break
        if done:
            break
except Exception as exc:
    errors.append(f"yandex:{type(exc).__name__}")

# Ozon: exact postings seen during/after the 27 Sep sync.
try:
    cid = os.getenv("OZON_CLIENT_ID", "").strip()
    secret = os.getenv("OZON_API_KEY", "").strip()
    h = {"Client-Id": cid, "Api-Key": secret, "Content-Type": "application/json"}
    for posting in TARGET_OZON:
        d = mp.ozon_posting_detail(h, posting)
        if not d:
            continue
        created = d.get("in_process_at") or d.get("created_at") or d.get("shipment_date")
        customer = dict(d.get("customer") or {}) if isinstance(d.get("customer"), dict) else {}
        addressee = d.get("addressee") if isinstance(d.get("addressee"), dict) else {}
        if not oss.s(customer.get("phone")) and oss.s(addressee.get("phone")):
            customer["phone"] = oss.s(addressee.get("phone"))
        if oss.s(addressee.get("pin")):
            customer["extension"] = oss.s(addressee.get("pin"))
        phone = oss.phone_text(customer)
        parts, total = [], 0
        for it in d.get("products") or []:
            if not isinstance(it, dict):
                continue
            n = quantity(it.get("quantity"))
            total += n
            name = oss.s(it.get("name") or it.get("offer_id"))
            parts.append(f"{name or 'Товар'} × {n}")
        add_row(created, "Ozon", posting, phone, "; ".join(parts), total)
except Exception as exc:
    errors.append(f"ozon:{type(exc).__name__}")

values = [["Дата заказа", "Источник", "Номер заказа", "Телефон", "Что заказали", "Количество"]]
values.extend(sorted(rows, key=lambda x: (x[0], x[1], x[2])))

info = oss.service_account_info(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", ""))
gc = gspread.service_account_from_dict(info)
sheet_id = os.getenv("GOOGLE_ORDERS_SHEET_ID", "").strip() or "1pAp6x67JTqFawps4D2sxSdEgGcxJN_ag6Qiy8Opoev4"
sh = gc.open_by_key(sheet_id)
try:
    ws = sh.worksheet(TAB)
except gspread.WorksheetNotFound:
    ws = sh.add_worksheet(title=TAB, rows=100, cols=6)
ws.clear()
ws.resize(rows=max(100, len(values) + 10), cols=6)
ws.update(range_name="A1", values=values, value_input_option="RAW")

print(f"recovered_rows={len(rows)}; source_errors={','.join(errors) if errors else 'none'}")
