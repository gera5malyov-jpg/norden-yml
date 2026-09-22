#!/usr/bin/env python3
import hashlib, os, re, sys, requests
from pathlib import Path

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE/"webasyst"))
from client import WebasystClient

TARGET=os.environ.get("TARGET_EXTERNAL_ID","30268053-0302-1").strip()
cid=os.environ["OZON_CLIENT_ID"].strip()
api=os.environ["OZON_API_KEY"].strip()
h={"Client-Id":cid,"Api-Key":api,"Content-Type":"application/json"}

def norm_phone(x):
    return re.sub(r"\D","",str(x or ""))

r=requests.post(
    "https://api-seller.ozon.ru/v3/posting/fbs/get",
    headers=h,
    json={"posting_number":TARGET,"with":{"analytics_data":False,"barcodes":False,"financial_data":False,"translit":False}},
    timeout=60,
)
r.raise_for_status()
d=r.json()
src=d.get("result") if isinstance(d,dict) and isinstance(d.get("result"),dict) else d
customer=src.get("customer") or {}
addr=customer.get("address") or {}
prr=src.get("prr_option") or {}
delivery=float(src.get("delivery_price") or 0)
lift=float(prr.get("price") or 0)
expected_shipping=delivery+lift

wa=WebasystClient(min_request_interval=0.2)
mp_key=hashlib.sha256(f"ozon|{TARGET}".encode()).hexdigest()[:32]
s=wa.call("shop.order.search",params={"hash":f"search/params.mp_key={mp_key}","limit":10,"fields":"*,contact_full,shipping_info"})
rows=s.get("orders") if isinstance(s,dict) else s
if not rows:
    raise SystemExit("VERIFY_WEBASYST_ORDER_NOT_FOUND=1")
info=wa.call("shop.order.getInfo",params={"id":rows[0]["id"]})
contact=info.get("contact") or {}
params=info.get("params") or {}

actual_name=str(contact.get("name") or "").strip()
actual_phone=str(contact.get("phone") or "").strip()
expected_name=str(customer.get("name") or "").strip()
expected_phone=str(customer.get("phone") or "").strip()

expected_street=str(addr.get("address_tail") or "").strip()
expected_city=str(addr.get("city") or "").strip()
expected_zip=str(addr.get("zip_code") or "").strip()
actual_street=str(params.get("shipping_address.street") or "").strip()
actual_city=str(params.get("shipping_address.city") or "").strip()
actual_zip=str(params.get("shipping_address.zip") or "").strip()
actual_shipping=float(info.get("shipping") or 0)

print("VERIFY_WEBASYST_ORDER_FOUND=1")
print("VERIFY_BUYER_NAME_MATCH="+("1" if expected_name and actual_name==expected_name else "0"))
print("VERIFY_BUYER_PHONE_MATCH="+("1" if expected_phone and norm_phone(actual_phone)==norm_phone(expected_phone) else "0"))
print("VERIFY_ADDRESS_STREET_MATCH="+("1" if expected_street and actual_street==expected_street else "0"))
print("VERIFY_ADDRESS_CITY_MATCH="+("1" if expected_city and actual_city==expected_city else "0"))
print("VERIFY_ADDRESS_ZIP_MATCH="+("1" if expected_zip and actual_zip==expected_zip else "0"))
print(f"VERIFY_EXPECTED_DELIVERY_PRICE={delivery:.2f}")
print(f"VERIFY_EXPECTED_LIFT_PRICE={lift:.2f}")
print(f"VERIFY_EXPECTED_SHIPPING_TOTAL={expected_shipping:.2f}")
print(f"VERIFY_ACTUAL_SHIPPING_TOTAL={actual_shipping:.2f}")
print("VERIFY_SHIPPING_MATCH="+("1" if abs(actual_shipping-expected_shipping)<0.01 else "0"))
print("VERIFY_RECIPIENT_MATCH="+("1" if expected_name and str(params.get("mp_recipient_name") or "").strip()==expected_name else "0"))
print("VERIFY_LIFT_PRICE_MATCH="+("1" if str(params.get("mp_lift_price") or "")!="" and abs(float(params.get("mp_lift_price") or 0)-lift)<0.01 else "0"))
print("VERIFY_LIFT_TYPE_MATCH="+("1" if str(params.get("mp_lift_type") or "").strip()==str(prr.get("code") or "").strip() else "0"))
print("VERIFY_REVERSE_STATUS_WRITES=0")
