#!/usr/bin/env python3
import json, os, requests

target=os.environ.get("TARGET_EXTERNAL_ID","30268053-0302-1").strip()
cid=os.environ["OZON_CLIENT_ID"].strip()
key=os.environ["OZON_API_KEY"].strip()
h={"Client-Id":cid,"Api-Key":key,"Content-Type":"application/json"}
body={
  "posting_number":target,
  "with":{"analytics_data":False,"barcodes":False,"financial_data":False,"translit":False}
}
r=requests.post("https://api-seller.ozon.ru/v3/posting/fbs/get",headers=h,json=body,timeout=60)
print("HTTP="+str(r.status_code))
if not r.ok:
    print("ERROR="+r.text[:300].replace(cid,"***").replace(key,"***"))
    raise SystemExit(1)
d=r.json()
root=d.get("result") if isinstance(d,dict) and isinstance(d.get("result"),dict) else d

def walk(obj,prefix="",depth=0):
    if depth>4:return
    if isinstance(obj,dict):
        for k,v in sorted(obj.items()):
            p=f"{prefix}.{k}" if prefix else str(k)
            print("FIELD="+p+" TYPE="+type(v).__name__)
            if isinstance(v,(dict,list)):
                walk(v,p,depth+1)
    elif isinstance(obj,list) and obj:
        print("LIST_SAMPLE="+prefix)
        walk(obj[0],prefix+"[]",depth+1)

walk(root)
