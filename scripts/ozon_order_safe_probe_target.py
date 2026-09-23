#!/usr/bin/env python3
import json, os, requests

target=os.environ["TARGET_EXTERNAL_ID"].strip()
cid=os.environ["OZON_CLIENT_ID"].strip()
key=os.environ["OZON_API_KEY"].strip()
h={"Client-Id":cid,"Api-Key":key,"Content-Type":"application/json"}
body={"posting_number":target,"with":{"analytics_data":False,"barcodes":False,"financial_data":False,"translit":False}}
r=requests.post("https://api-seller.ozon.ru/v3/posting/fbs/get",headers=h,json=body,timeout=60)
print("HTTP="+str(r.status_code))
if not r.ok:
    print("ERROR="+r.text[:500].replace(cid,"***").replace(key,"***"))
    raise SystemExit(1)
d=r.json()
o=d.get("result") if isinstance(d,dict) and isinstance(d.get("result"),dict) else d
customer=o.get("customer") if isinstance(o.get("customer"),dict) else {}
address=customer.get("address") if isinstance(customer.get("address"),dict) else {}
products=[]
for p in o.get("products") or []:
    if not isinstance(p,dict): continue
    products.append({
        "name":p.get("name"),
        "offer_id":p.get("offer_id"),
        "quantity":p.get("quantity"),
        "price":p.get("price"),
        "currency_code":p.get("currency_code"),
        "sku":p.get("sku"),
    })
safe={
    "posting_number":o.get("posting_number"),
    "status":o.get("status"),
    "substatus":o.get("substatus"),
    "delivering_date":o.get("delivering_date"),
    "shipment_date":o.get("shipment_date"),
    "products":products,
    "customer_fields_present":{
        "name":bool(str(customer.get("name") or "").strip()),
        "phone":bool(str(customer.get("phone") or "").strip()),
        "address":bool(address),
    },
    "destination":{
        "country":address.get("country"),
        "region":address.get("region"),
        "city":address.get("city"),
        "zip_present":bool(str(address.get("zip_code") or "").strip()),
        "street_present":bool(str(address.get("address_tail") or "").strip()),
    },
    "delivery_price":o.get("delivery_price"),
    "prr_option":o.get("prr_option"),
}
text=json.dumps(safe,ensure_ascii=False,indent=2)
print(text)
out_path=os.environ.get("SAFE_RESULT_PATH","").strip()
if out_path:
    os.makedirs(os.path.dirname(out_path),exist_ok=True)
    with open(out_path,"w",encoding="utf-8") as f:
        f.write(text+"\n")
