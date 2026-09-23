#!/usr/bin/env python3
from __future__ import annotations
import json, os, requests

ORDERS=["67081442-0604-1","37307681-0157-1","80399471-0026-1"]
OUT=os.environ.get("SAFE_RESULT_PATH","ozon/prr_batch_2026-09-23.json")
CID=os.environ["OZON_CLIENT_ID"].strip()
KEY=os.environ["OZON_API_KEY"].strip()

def s(v): return str(v or "").strip()

def get_order(n):
    r=requests.post(
        "https://api-seller.ozon.ru/v3/posting/fbs/get",
        headers={"Client-Id":CID,"Api-Key":KEY,"Content-Type":"application/json"},
        json={"posting_number":n,"with":{"analytics_data":False,"barcodes":False,"financial_data":False,"translit":False}},
        timeout=60
    )
    r.raise_for_status()
    d=r.json()
    return d.get("result") if isinstance(d,dict) and isinstance(d.get("result"),dict) else d

rows=[]
for n in ORDERS:
    o=get_order(n)
    prr=o.get("prr_option") if isinstance(o.get("prr_option"),dict) else {}
    customer=o.get("customer") if isinstance(o.get("customer"),dict) else {}
    addr=customer.get("address") if isinstance(customer.get("address"),dict) else {}
    rows.append({
        "order_number":n,
        "prr_option":prr,
        "delivery_price":s(o.get("delivery_price")),
        "address_comment_present":bool(s(addr.get("comment"))),
        "address_comment_length":len(s(addr.get("comment"))),
    })

os.makedirs(os.path.dirname(OUT),exist_ok=True)
with open(OUT,"w",encoding="utf-8") as f:
    json.dump(rows,f,ensure_ascii=False,indent=2)
    f.write("\n")
print(json.dumps(rows,ensure_ascii=False,indent=2))
