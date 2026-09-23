#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
import requests
import xml.etree.ElementTree as ET

OZON_BASE = "https://api-seller.ozon.ru"
DALLI_BASE = "https://api.dalli-service.com/v1/"
ORDER_NO = os.getenv("TARGET_EXTERNAL_ID", "97426764-0177-1").strip()
OUT_PATH = os.getenv("SAFE_RESULT_PATH", "dalli/order_97426764-0177-1_msk_basket_result.json").strip()

def s(v):
    return str(v or "").strip()

def ozon_post(path, body):
    cid = s(os.environ.get("OZON_CLIENT_ID"))
    key = s(os.environ.get("OZON_API_KEY"))
    if not cid or not key:
        raise RuntimeError("Не заданы OZON_CLIENT_ID/OZON_API_KEY")
    r = requests.post(
        OZON_BASE + path,
        headers={"Client-Id": cid, "Api-Key": key, "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"Ozon API {path} HTTP {r.status_code}: {r.text[:300]}")
    return r.json()

def dalli_post(root):
    token = s(os.environ.get("DALLI_TOKEN_MSK"))
    if not token:
        raise RuntimeError("Не задан GitHub Secret DALLI_TOKEN_MSK")
    auth = ET.Element("auth", {"token": token})
    root.insert(0, auth)
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    r = requests.post(
        DALLI_BASE,
        data=payload,
        headers={
            "Content-Type": "application/xml; charset=utf-8",
            "Accept": "application/xml, text/xml, */*",
            "User-Agent": "megapolis-dalli-msk/1.0",
        },
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"Dalli HTTP {r.status_code}: {r.text[:500]}")
    try:
        return ET.fromstring(r.content)
    except ET.ParseError as e:
        raise RuntimeError(f"Dalli вернул некорректный XML: {r.text[:500]}") from e

def get_ozon_order():
    data = ozon_post("/v3/posting/fbs/get", {
        "posting_number": ORDER_NO,
        "with": {
            "analytics_data": False,
            "barcodes": False,
            "financial_data": False,
            "translit": False,
        },
    })
    return data.get("result") if isinstance(data, dict) and isinstance(data.get("result"), dict) else data

def full_address(order):
    customer = order.get("customer") if isinstance(order.get("customer"), dict) else {}
    a = customer.get("address") if isinstance(customer.get("address"), dict) else {}
    parts = []
    for key in ("zip_code", "country", "region", "city", "address_tail"):
        v = s(a.get(key))
        if v and v not in parts:
            parts.append(v)
    return ", ".join(parts), a

def list_services():
    root = dalli_post(ET.Element("services"))
    out = {}
    for node in root.findall(".//service"):
        code = s(node.findtext("code"))
        name = s(node.findtext("name"))
        if code:
            out[code] = name
    return out

def choose_service(address_dict, services):
    text = " ".join([
        s(address_dict.get("region")),
        s(address_dict.get("city")),
    ]).lower()
    candidates = []
    if "санкт-петербург" in text or "ленинград" in text:
        candidates = ["11", "22"]
    elif "москва" in text or "москов" in text:
        candidates = ["1", "22"]
    else:
        candidates = ["22", "1", "11"]
    for code in candidates:
        if code in services:
            return code, services[code]
    raise RuntimeError("В договоре Dalli МСК нет подходящего курьерского типа доставки")

def get_dates(address, service):
    root = ET.Element("intervals")
    ET.SubElement(root, "address").text = address
    ET.SubElement(root, "service").text = service
    ET.SubElement(root, "strict").text = "T"
    ET.SubElement(root, "output").text = "dates"
    ET.SubElement(root, "format").text = "minutes"
    ans = dalli_post(root)
    dates = []
    for d in ans.findall(".//date"):
        value = s(d.get("value"))
        intervals = []
        for iv in d.findall("./intervals/interval"):
            tmin = s(iv.findtext("time_min"))
            tmax = s(iv.findtext("time_max"))
            typ = s(iv.get("type"))
            if tmin and tmax:
                intervals.append((tmin, tmax, typ))
        if value and intervals:
            dates.append((value, intervals))
    return dates

def choose_date_interval(dates):
    if not dates:
        raise RuntimeError("Dalli не вернул доступных дат/интервалов для адреса")

    def minutes(x):
        h, m = (x.split(":") + ["0"])[:2]
        return int(h) * 60 + int(m)

    # earliest date; within it prefer the widest BASIC interval, then widest available
    dates = sorted(dates, key=lambda x: x[0])
    date, intervals = dates[0]
    basic = [x for x in intervals if x[2].lower() == "basic"] or intervals
    interval = max(basic, key=lambda x: minutes(x[1]) - minutes(x[0]))
    return date, interval[0], interval[1], interval[2]

def getbasket():
    root = ET.Element("getbasket")
    ET.SubElement(root, "number").text = ORDER_NO
    return dalli_post(root)

def basket_snapshot(root):
    order = root.find(".//order")
    if order is None:
        return None
    barcode = s(order.findtext("barcode"))
    errors = []
    for e in order.findall(".//errors/error"):
        errors.append({
            "code": s(e.get("errorCode")),
            "field": s(e.get("error")),
            "message": s(e.get("errorMessage")),
        })
    return {
        "barcode": barcode,
        "service": s(order.findtext("service")),
        "date": s(order.findtext("./receiver/date")),
        "time_min": s(order.findtext("./receiver/time_min")),
        "time_max": s(order.findtext("./receiver/time_max")),
        "errors": errors,
    }

def create_basket(order, address, service, date, tmin, tmax):
    customer = order.get("customer") if isinstance(order.get("customer"), dict) else {}
    person = s(customer.get("name"))
    phone = s(customer.get("phone"))
    if not person:
        raise RuntimeError("Ozon не вернул ФИО получателя")
    if not phone:
        raise RuntimeError("Ozon не вернул телефон получателя")
    if not address:
        raise RuntimeError("Ozon не вернул адрес получателя")

    products = [x for x in (order.get("products") or []) if isinstance(x, dict)]
    if not products:
        raise RuntimeError("В заказе Ozon нет товаров")

    total = 0.0
    for p in products:
        try:
            total += float(p.get("price") or 0) * max(1, int(p.get("quantity") or 1))
        except Exception:
            pass

    root = ET.Element("basketcreate")
    # ВАЖНО: autosend намеренно НЕ указывается.
    node = ET.SubElement(root, "order", {"number": ORDER_NO})
    receiver = ET.SubElement(node, "receiver")
    ET.SubElement(receiver, "address").text = address
    ET.SubElement(receiver, "person").text = person
    ET.SubElement(receiver, "phone").text = phone
    ET.SubElement(receiver, "date").text = date
    ET.SubElement(receiver, "time_min").text = tmin
    ET.SubElement(receiver, "time_max").text = tmax
    ET.SubElement(node, "service").text = service
    ET.SubElement(node, "quantity").text = "1"
    ET.SubElement(node, "paytype").text = "NO"
    ET.SubElement(node, "price").text = "0"
    ET.SubElement(node, "inshprice").text = f"{total:.2f}"
    ET.SubElement(node, "instruction").text = (
        f"Заказ Ozon {ORDER_NO}. Предоплачен. "
        "Создан только в корзине Dalli, без автоматической отправки в доставку."
    )
    items = ET.SubElement(node, "items")
    for p in products:
        qty = max(1, int(p.get("quantity") or 1))
        price = float(p.get("price") or 0)
        attrs = {
            "quantity": str(qty),
            "retprice": f"{price:.2f}",
            "inshprice": f"{price:.2f}",
            "article": s(p.get("offer_id"))[:100],
        }
        item = ET.SubElement(items, "item", attrs)
        item.text = s(p.get("name"))[:250] or s(p.get("offer_id")) or "Товар Ozon"

    return dalli_post(root), total, products

def save_result(result):
    os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))

def main():
    ozon = get_ozon_order()
    address, address_dict = full_address(ozon)
    destination_city = s(address_dict.get("city"))
    products_safe = [
        {
            "name": s(p.get("name")),
            "offer_id": s(p.get("offer_id")),
            "quantity": int(p.get("quantity") or 1),
            "price": s(p.get("price")),
        }
        for p in (ozon.get("products") or [])
        if isinstance(p, dict)
    ]

    existing = basket_snapshot(getbasket())
    if existing is not None:
        save_result({
            "ok": True,
            "already_existed": True,
            "order_number": ORDER_NO,
            "account": "МСК",
            "endpoint": DALLI_BASE,
            "in_basket": True,
            "autosend": False,
            "destination_city": destination_city,
            "products": products_safe,
            **existing,
        })
        return 0

    services = list_services()
    service, service_name = choose_service(address_dict, services)
    dates = get_dates(address, service)
    date, tmin, tmax, interval_type = choose_date_interval(dates)

    create_resp, declared_value, _ = create_basket(
        ozon, address, service, date, tmin, tmax
    )
    order_resp = create_resp.find(".//order")
    errors = []
    barcode = ""
    if order_resp is not None:
        success = order_resp.find("success")
        if success is not None:
            barcode = s(success.get("barcode"))
        for e in order_resp.findall("error"):
            errors.append({
                "code": s(e.get("errorCode")),
                "field": s(e.get("error")),
                "message": s(e.get("errorMessage")),
            })
    if errors and not barcode:
        # If API reports duplicate, verify basket before failing.
        existing = basket_snapshot(getbasket())
        if existing is None:
            raise RuntimeError("Dalli basketcreate: " + "; ".join(x["message"] for x in errors))
    verified = basket_snapshot(getbasket())
    if verified is None:
        raise RuntimeError("Dalli не подтвердил наличие заказа в корзине после basketcreate")

    save_result({
        "ok": True,
        "already_existed": False,
        "order_number": ORDER_NO,
        "account": "МСК",
        "endpoint": DALLI_BASE,
        "in_basket": True,
        "autosend": False,
        "service": service,
        "service_name": service_name,
        "date": date,
        "time_min": tmin,
        "time_max": tmax,
        "interval_type": interval_type,
        "barcode": verified.get("barcode") or barcode,
        "destination_city": destination_city,
        "declared_value": round(declared_value, 2),
        "paytype": "NO",
        "products": products_safe,
        "basket_errors": verified.get("errors") or [],
    })
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        safe = {
            "ok": False,
            "order_number": ORDER_NO,
            "account": "МСК",
            "in_basket": False,
            "autosend": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        save_result(safe)
        raise
