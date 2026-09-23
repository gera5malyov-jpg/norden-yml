#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import requests
import xml.etree.ElementTree as ET

OZON_BASE="https://api-seller.ozon.ru"
DALLI_BASE="https://api.dalli-service.com/v1/"
ORDER_NO=os.environ["TARGET_EXTERNAL_ID"].strip()
OUT_PATH=os.environ["SAFE_RESULT_PATH"].strip()

def s(v):
    return str(v or "").strip()

def ozon_order():
    cid=s(os.environ.get("OZON_CLIENT_ID"))
    key=s(os.environ.get("OZON_API_KEY"))
    r=requests.post(
        OZON_BASE+"/v3/posting/fbs/get",
        headers={"Client-Id":cid,"Api-Key":key,"Content-Type":"application/json"},
        json={"posting_number":ORDER_NO,"with":{"analytics_data":False,"barcodes":False,"financial_data":False,"translit":False}},
        timeout=60,
    )
    r.raise_for_status()
    d=r.json()
    return d.get("result") if isinstance(d,dict) and isinstance(d.get("result"),dict) else d

def dalli_getbasket():
    token=s(os.environ.get("DALLI_TOKEN_MSK"))
    root=ET.Element("getbasket")
    root.insert(0,ET.Element("auth",{"token":token}))
    ET.SubElement(root,"number").text=ORDER_NO
    r=requests.post(
        DALLI_BASE,
        data=ET.tostring(root,encoding="utf-8",xml_declaration=True),
        headers={"Content-Type":"application/xml; charset=utf-8","Accept":"application/xml, text/xml, */*"},
        timeout=60,
    )
    r.raise_for_status()
    return ET.fromstring(r.content)

NOTE_KEYS=("comment","customer_comment","delivery_comment","recipient_comment","comment_to_delivery","order_comment","note","notes")
def customer_note(order):
    c=order.get("customer") if isinstance(order.get("customer"),dict) else {}
    a=c.get("address") if isinstance(c.get("address"),dict) else {}
    for scope in (order,c,a):
        for key in NOTE_KEYS:
            v=scope.get(key)
            if isinstance(v,str) and v.strip():
                return v.strip()
    return ""

def main():
    o=ozon_order()
    addressee=o.get("addressee") if isinstance(o.get("addressee"),dict) else {}
    customer=o.get("customer") if isinstance(o.get("customer"),dict) else {}
    base_phone=s(addressee.get("phone") or customer.get("phone"))
    pin=s(addressee.get("pin"))
    expected_phone=base_phone+(f" доб. {pin}" if pin else "")
    expected_note=customer_note(o)

    root=dalli_getbasket()
    order=root.find(".//order")
    if order is None:
        result={"ok":False,"order_number":ORDER_NO,"in_basket":False,"error":"Заказ не найден в корзине Dalli"}
    else:
        phone=s(order.findtext("./receiver/phone"))
        instruction=s(order.findtext("instruction"))
        items=order.findall("./items/item")
        vat_values=[s(x.get("VATrate")) for x in items]
        result={
            "ok":True,
            "order_number":ORDER_NO,
            "account":"МСК",
            "in_basket":True,
            "sent_to_delivery":False,
            "barcode":s(order.findtext("barcode")),
            "phone_matches_ozon_full":phone==expected_phone,
            "phone_has_extension":bool(pin),
            "phone_extension_length":len(pin),
            "note_present_in_ozon":bool(expected_note),
            "instruction_matches_ozon_note":instruction==expected_note,
            "instruction_empty":instruction=="",
            "vat_values":vat_values,
            "vat_all_zero":bool(vat_values) and all(v=="0" for v in vat_values),
            "errors":[],
        }
    os.makedirs(os.path.dirname(OUT_PATH),exist_ok=True)
    with open(OUT_PATH,"w",encoding="utf-8") as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
        f.write("\n")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not result.get("ok"):
        raise SystemExit(1)
    if not result.get("phone_matches_ozon_full"):
        raise SystemExit(2)
    if not result.get("instruction_matches_ozon_note"):
        raise SystemExit(3)
    if not result.get("vat_all_zero"):
        raise SystemExit(4)

if __name__=="__main__":
    main()
