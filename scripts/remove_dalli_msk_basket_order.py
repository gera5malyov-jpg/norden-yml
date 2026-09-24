#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
import requests

BASE = "https://api.dalli-service.com/v1/"
ORDER_NO = os.environ["TARGET_EXTERNAL_ID"].strip()
BARCODE = os.environ["DALLI_BARCODE"].strip()
OUT_PATH = os.environ.get("SAFE_RESULT_PATH", "").strip()

def token():
    value = (os.environ.get("DALLI_TOKEN_MSK") or "").strip()
    if not value:
        raise RuntimeError("Не задан DALLI_TOKEN_MSK")
    return value

def post(root):
    root.insert(0, ET.Element("auth", {"token": token()}))
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    r = requests.post(
        BASE,
        data=payload,
        headers={"Content-Type": "application/xml; charset=utf-8", "Accept": "application/xml, text/xml, */*"},
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"Dalli HTTP {r.status_code}: {r.text[:500]}")
    return ET.fromstring(r.content)

def getbasket():
    root = ET.Element("getbasket")
    ET.SubElement(root, "number").text = ORDER_NO
    return post(root)

def exists_in_basket():
    return getbasket().find(".//order") is not None

def remove():
    root = ET.Element("removebasket")
    ET.SubElement(root, "barcode").text = BARCODE
    ET.SubElement(root, "number").text = ORDER_NO
    return post(root)

def save(data):
    if OUT_PATH:
        os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
    print(json.dumps(data, ensure_ascii=False, indent=2))

def main():
    before = exists_in_basket()
    if before:
        remove()
    after = exists_in_basket()
    if after:
        raise RuntimeError("Заказ остался в корзине Dalli МСК после removebasket")
    save({
        "ok": True,
        "order_number": ORDER_NO,
        "barcode": BARCODE,
        "account": "МСК",
        "was_in_basket": before,
        "in_basket_after": after,
        "removed": before and not after,
    })

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        save({
            "ok": False,
            "order_number": ORDER_NO,
            "barcode": BARCODE,
            "account": "МСК",
            "error": f"{type(exc).__name__}: {exc}",
        })
        raise
