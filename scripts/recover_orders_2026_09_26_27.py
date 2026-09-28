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

LABEL = {
    "yandex_market": "Яндекс Маркет",
    "ozon": "Ozon",
    "wildberries": "Wildberries",
    "yandex_kit": "Яндекс KIT",
    "webasyst": "Сайт/Webasyst",
}


def local_date(value):
    x = mp.dt(value)
    return x.astimezone(MSK).strftime("%Y-%m-%d") if x else ""


def quantity(value):
    try:
        return max(1, int(float(str(value or 1).replace(",", "."))))
    except Exception:
        return 1


wa = WebasystClient(min_request_interval=0.15)
rows = {}
errors = []


def put(source, ext, created, phone, items, qty):
    d = local_date(created)
    if d not in TARGET_DATES:
        return
    rows[(source, str(ext))] = {
        "date": d,
        "source": LABEL.get(source, source),
        "order_no": str(ext),
        "phone": phone or "",
        "items": items or "",
        "qty": qty or 0,
    }


# 1) Exact Yandex order seen by the scheduled sync on 26 Sep.
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

    for bid in business_ids:
        page = None
        found = False
        while True:
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
                    name = oss.s(it.get("offerName") or it.get("name") or it.get("offerId") or it.get("shopSku"))
                    parts.append(f"{name or 'Товар'} × {n}")
                put("yandex_market", oid, o.get("creationDate"), phone, "; ".join(parts), total)
                found = True
            if found:
                break
            paging = root.get("paging") if isinstance(root, dict) else None
            page = (paging or {}).get("nextPageToken") if isinstance(paging, dict) else None
            if not page:
                break
        if found:
            break
except Exception as exc:
    errors.append(f"yandex:{type(exc).__name__}")


# 2) Ozon orders around the outage. The source timestamp decides whether the
# second order belongs to 27 Sep or to 28 Sep.
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
        put("ozon", posting, created, phone, "; ".join(parts), total)
except Exception as exc:
    errors.append(f"ozon:{type(exc).__name__}")


# 3) Current Webasyst: enrich marketplace rows from Shop-Script and include any
# direct site orders whose own creation date is 26/27 Sep.
try:
    p = wa.call("shop.order.search", params={"limit": 100, "offset": 0, "fields": "*,state"})
    batch = oss.listify((p or {}).get("orders") if isinstance(p, dict) else p)
    for summary in batch:
        oid = oss.s(summary.get("id"))
        if not oid:
            continue
        try:
            info = wa.call("shop.order.getInfo", params={"id": oid})
        except Exception:
            info = summary
        if not isinstance(info, dict):
            continue
        params = dict(info.get("params") or {}) if isinstance(info.get("params"), dict) else {}
        source = oss.s(params.get("mp_source")) or "webasyst"
        ext = oss.s(params.get("mp_external_id")) or oss.s(info.get("id_str")) or oid
        source_created = oss.s(params.get("mp_created_at"))
        own_created = oss.s(info.get("create_datetime") or summary.get("create_datetime"))
        created = source_created or own_created

        products, qty = oss.item_text(info.get("items"), {})
        contact = info.get("contact") if isinstance(info.get("contact"), dict) else {}
        phone = oss.phone_text(contact)
        saved_ext = oss.s(params.get("mp_phone_extension"))
        if phone and saved_ext and f"доб. {saved_ext}" not in phone:
            phone += f" доб. {saved_ext}"

        key = (source, ext)
        if key in rows:
            if phone:
                rows[key]["phone"] = phone
            if products:
                rows[key]["items"] = products
                rows[key]["qty"] = qty
        elif local_date(created) in TARGET_DATES:
            put(source, ext, created, phone, products, qty)
except Exception as exc:
    errors.append(f"webasyst:{type(exc).__name__}")


values = [["Дата заказа", "Источник", "Номер заказа", "Телефон", "Что заказали", "Количество"]]
for r in sorted(rows.values(), key=lambda x: (x["date"], x["source"], x["order_no"])):
    values.append([r["date"], r["source"], r["order_no"], r["phone"], r["items"], r["qty"]])

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

print(f"recovered_rows={len(values)-1}; source_errors={','.join(errors) if errors else 'none'}")
