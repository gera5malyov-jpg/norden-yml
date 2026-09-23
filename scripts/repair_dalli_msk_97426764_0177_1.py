#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import requests
import xml.etree.ElementTree as ET

OZON_BASE = "https://api-seller.ozon.ru"
DALLI_BASE = "https://api.dalli-service.com/v1/"
ORDER_NO = os.getenv("TARGET_EXTERNAL_ID", "97426764-0177-1").strip()
OUT_PATH = os.getenv("SAFE_RESULT_PATH", "dalli/order_97426764-0177-1_msk_repair_result.json").strip()

def s(v):
    return str(v or "").strip()

def ozon_order():
    cid=s(os.environ.get("OZON_CLIENT_ID"))
    key=s(os.environ.get("OZON_API_KEY"))
    if not cid or not key:
        raise RuntimeError("Не заданы OZON_CLIENT_ID/OZON_API_KEY")
    r=requests.post(
        OZON_BASE+"/v3/posting/fbs/get",
        headers={"Client-Id":cid,"Api-Key":key,"Content-Type":"application/json"},
        json={"posting_number":ORDER_NO,"with":{"analytics_data":False,"barcodes":False,"financial_data":False,"translit":False}},
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"Ozon HTTP {r.status_code}: {r.text[:300]}")
    d=r.json()
    return d.get("result") if isinstance(d,dict) and isinstance(d.get("result"),dict) else d

def dalli_post(root):
    token=s(os.environ.get("DALLI_TOKEN_MSK"))
    if not token:
        raise RuntimeError("Не задан DALLI_TOKEN_MSK")
    root.insert(0, ET.Element("auth", {"token":token}))
    payload=ET.tostring(root,encoding="utf-8",xml_declaration=True)
    r=requests.post(
        DALLI_BASE,
        data=payload,
        headers={"Content-Type":"application/xml; charset=utf-8","Accept":"application/xml, text/xml, */*","User-Agent":"megapolis-dalli-msk/1.1"},
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"Dalli HTTP {r.status_code}: {r.text[:500]}")
    return ET.fromstring(r.content)

def getbasket():
    root=ET.Element("getbasket")
    ET.SubElement(root,"number").text=ORDER_NO
    return dalli_post(root)

def basket_order(root):
    return root.find(".//order")

def address_from_ozon(o):
    c=o.get("customer") if isinstance(o.get("customer"),dict) else {}
    a=c.get("address") if isinstance(c.get("address"),dict) else {}
    vals=[]
    for k in ("zip_code","country","region","city","address_tail"):
        v=s(a.get(k))
        if v and v not in vals:
            vals.append(v)
    return ", ".join(vals)

NOTE_KEYS=(
    "comment","customer_comment","delivery_comment","recipient_comment",
    "comment_to_delivery","order_comment","note","notes"
)
def customer_note(o):
    scopes=[o]
    c=o.get("customer")
    if isinstance(c,dict):
        scopes.append(c)
        a=c.get("address")
        if isinstance(a,dict):
            scopes.append(a)
    for scope in scopes:
        for k in NOTE_KEYS:
            v=scope.get(k)
            if isinstance(v,str) and v.strip():
                return v.strip()
    return ""

def add_text(parent, tag, value):
    el=ET.SubElement(parent,tag)
    el.text=s(value)
    return el

def safe_float(v, default=0.0):
    try:
        return float(str(v).replace(",","."))
    except Exception:
        return default

def repair():
    o=ozon_order()
    current_root=getbasket()
    cur=basket_order(current_root)
    if cur is None:
        raise RuntimeError("Заказ не найден в корзине Dalli МСК")

    customer=o.get("customer") if isinstance(o.get("customer"),dict) else {}
    addressee=o.get("addressee") if isinstance(o.get("addressee"),dict) else {}
    person=s(addressee.get("name") or customer.get("name"))
    base_phone=s(addressee.get("phone") or customer.get("phone"))
    phone_pin=s(addressee.get("pin"))
    phone=base_phone + (f" доб. {phone_pin}" if phone_pin else "")
    address=address_from_ozon(o)
    if not person or not phone or not address:
        raise RuntimeError("Ozon не вернул ФИО/телефон/адрес полностью")

    note=customer_note(o)
    barcode=s(cur.findtext("barcode"))
    if not barcode:
        raise RuntimeError("Dalli не вернул barcode заказа")

    root=ET.Element("editbasket")
    order=ET.SubElement(root,"order",{"number":ORDER_NO})
    add_text(order,"barcode",barcode)

    recv=ET.SubElement(order,"receiver")
    add_text(recv,"address",address)
    add_text(recv,"person",person)
    # Передаём телефон ровно как он пришёл из Ozon, включая добавочный.
    add_text(recv,"phone",phone)
    add_text(recv,"date",s(cur.findtext("./receiver/date"))[:10])
    add_text(recv,"time_min",s(cur.findtext("./receiver/time_min"))[:5])
    add_text(recv,"time_max",s(cur.findtext("./receiver/time_max"))[:5])

    add_text(order,"service",cur.findtext("service"))
    if s(cur.findtext("weight")):
        add_text(order,"weight",cur.findtext("weight"))
    add_text(order,"quantity",cur.findtext("quantity") or "1")
    add_text(order,"paytype",cur.findtext("paytype") or "NO")
    if s(cur.findtext("priced")):
        add_text(order,"priced",cur.findtext("priced"))
    add_text(order,"price",cur.findtext("price") or "0")
    add_text(order,"inshprice",cur.findtext("inshprice") or "0")

    # В примечание — только комментарий покупателя из заказа Ozon.
    add_text(order,"instruction",note)

    items=ET.SubElement(order,"items")
    products=[p for p in (o.get("products") or []) if isinstance(p,dict)]
    if not products:
        raise RuntimeError("В Ozon-заказе нет товаров")
    for p in products:
        qty=max(1,int(p.get("quantity") or 1))
        price=safe_float(p.get("price"))
        attrs={
            "quantity":str(qty),
            "retprice":f"{price:.2f}",
            "inshprice":f"{price:.2f}",
            "article":s(p.get("offer_id"))[:100],
            "VATrate":"0",
        }
        it=ET.SubElement(items,"item",attrs)
        it.text=s(p.get("name"))[:250] or s(p.get("offer_id")) or "Товар Ozon"

    resp=dalli_post(root)
    errors=[]
    for e in resp.findall(".//error"):
        errors.append({
            "code":s(e.get("errorCode")),
            "field":s(e.get("error")),
            "message":s(e.get("errorMessage")),
        })
    if errors:
        raise RuntimeError("Dalli editbasket: "+"; ".join(x["message"] for x in errors))

    verify_root=getbasket()
    v=basket_order(verify_root)
    if v is None:
        raise RuntimeError("После editbasket заказ не найден в корзине")

    vphone=s(v.findtext("./receiver/phone"))
    vinstruction=s(v.findtext("instruction"))
    vitems=v.findall("./items/item")
    vat_values=[s(x.get("VATrate")) for x in vitems]
    # Некоторые ответы getbasket могут не возвращать VATrate. Тогда наличие ставки подтверждено успешным editbasket;
    # если атрибуты возвращаются, они обязаны быть 0.
    vat_verified=(not vat_values) or all(x=="0" for x in vat_values)
    # Проверка добавочного: главное — Dalli сохранил строку телефона полностью и без изменения.
    exact_phone=(vphone==phone)

    result={
        "ok": True,
        "order_number": ORDER_NO,
        "account":"МСК",
        "in_basket": True,
        "sent_to_delivery": False,
        "barcode": s(v.findtext("barcode")) or barcode,
        "phone_exactly_from_ozon": exact_phone,
        "phone_has_extension": bool(phone_pin),
        "phone_extension_length": len(phone_pin),
        "phone_length": len(vphone),
        "note_present_in_ozon": bool(note),
        "instruction_matches_ozon_note": vinstruction==note,
        "instruction_empty": vinstruction=="",
        "vat_requested": 0,
        "vat_verified": vat_verified,
        "vat_values_returned_by_getbasket": vat_values,
        "errors": [],
    }
    os.makedirs(os.path.dirname(OUT_PATH) or ".",exist_ok=True)
    with open(OUT_PATH,"w",encoding="utf-8") as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
        f.write("\n")
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":
    try:
        repair()
    except Exception as exc:
        result={
            "ok":False,
            "order_number":ORDER_NO,
            "account":"МСК",
            "in_basket":True,
            "sent_to_delivery":False,
            "error":f"{type(exc).__name__}: {exc}",
        }
        os.makedirs(os.path.dirname(OUT_PATH) or ".",exist_ok=True)
        with open(OUT_PATH,"w",encoding="utf-8") as f:
            json.dump(result,f,ensure_ascii=False,indent=2)
            f.write("\n")
        print(json.dumps(result,ensure_ascii=False,indent=2))
        raise
