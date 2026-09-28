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
MSK = timezone(timedelta(hours=3))
TAB = "Восстановление 26-27"
SOURCE_LABEL = {
    "yandex_market": "Яндекс Маркет",
    "ozon": "Ozon",
    "wildberries": "Wildberries",
    "yandex_kit": "Яндекс KIT",
    "webasyst": "Сайт/Webasyst",
}


def local_date(value):
    x = mp.dt(value)
    return x.astimezone(MSK).strftime("%Y-%m-%d") if x else ""


wa = WebasystClient(min_request_interval=0.20)
name_cache = {}


def sku_name(code):
    code = oss.s(code)
    if not code:
        return ""
    if code in name_cache:
        return name_cache[code]
    name = ""
    try:
        p = wa.call("shop.product.search", params={
            "hash": f"search/query={code}",
            "limit": 50,
            "fields": "id,name,skus",
        })
        products = oss.listify((p or {}).get("products") if isinstance(p, dict) else p)
        for prod in products:
            skus = oss.listify(prod.get("skus"))
            if any(oss.s(x.get("sku")) == code for x in skus):
                name = oss.s(prod.get("name"))
                if name:
                    break
    except Exception:
        pass
    name_cache[code] = name or code
    return name_cache[code]


def items_text(items):
    parts = []
    total = 0
    for it in oss.listify(items):
        try:
            qty = max(1, int(float(str(it.get("quantity", 1)).replace(",", "."))))
        except Exception:
            qty = 1
        sku = oss.s(it.get("sku") or it.get("sku_code") or it.get("offer_id"))
        name = oss.s(it.get("name") or it.get("product_name")) or sku_name(sku) or "Товар"
        parts.append(f"{name} × {qty}")
        total += qty
    return "; ".join(parts), total


def direct_phone(o):
    buyer = o.get("buyer") if isinstance(o.get("buyer"), dict) else {}
    phone = oss.phone_text(buyer)
    if not phone:
        phone = oss.format_phone(
            buyer.get("phone") or buyer.get("phoneNumber") or buyer.get("mobilePhone")
        )
    return phone


rows = {}
errors = []

for source, loader in (
    ("yandex_market", mp.load_yandex_market),
    ("wildberries", mp.load_wb),
    ("yandex_kit", mp.load_kit),
    ("ozon", mp.load_ozon),
):
    try:
        data = loader()
    except Exception as exc:
        errors.append(f"{source}:{type(exc).__name__}")
        continue
    for o in data:
        d = local_date(o.get("created_at"))
        if d not in TARGET_DATES:
            continue
        ext = oss.s(o.get("external_id"))
        if not ext:
            continue
        products, qty = items_text(o.get("items"))
        rows[(source, ext)] = {
            "date": d,
            "source": SOURCE_LABEL[source],
            "order_no": ext,
            "phone": direct_phone(o),
            "items": products,
            "qty": qty,
        }

# Read recent Webasyst orders. This enriches marketplace rows with the phone and
# product names saved in Shop-Script and also catches direct site orders.
offset = 0
for _ in range(10):
    try:
        p = wa.call("shop.order.search", params={
            "offset": offset,
            "limit": 100,
            "fields": "*,state",
        })
    except Exception as exc:
        errors.append(f"webasyst_search:{type(exc).__name__}")
        break
    batch = oss.listify((p or {}).get("orders") if isinstance(p, dict) else p)
    if not batch:
        break
    stop_old = True
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
        d = local_date(created)
        if d >= "2026-09-25":
            stop_old = False

        key = (source, ext)
        contact = info.get("contact") if isinstance(info.get("contact"), dict) else {}
        phone = oss.phone_text(contact)
        stored_ext = oss.s(params.get("mp_phone_extension"))
        if phone and stored_ext and f"доб. {stored_ext}" not in phone:
            phone += f" доб. {stored_ext}"
        products, qty = oss.item_text(info.get("items"), {})

        if key in rows:
            if phone:
                rows[key]["phone"] = phone
            if products:
                rows[key]["items"] = products
                rows[key]["qty"] = qty
        elif source == "webasyst" and d in TARGET_DATES:
            rows[key] = {
                "date": d,
                "source": SOURCE_LABEL["webasyst"],
                "order_no": ext,
                "phone": phone,
                "items": products,
                "qty": qty,
            }

    if len(batch) < 100 or stop_old:
        break
    offset += len(batch)

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
