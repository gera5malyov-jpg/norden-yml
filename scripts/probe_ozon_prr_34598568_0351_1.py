#!/usr/bin/env python3
import json, os, requests
ORDER="34598568-0351-1"
r=requests.post("https://api-seller.ozon.ru/v3/posting/fbs/get",
 headers={"Client-Id":os.environ["OZON_CLIENT_ID"],"Api-Key":os.environ["OZON_API_KEY"],"Content-Type":"application/json"},
 json={"posting_number":ORDER,"with":{"analytics_data":False,"barcodes":False,"financial_data":False,"translit":False}},timeout=60)
r.raise_for_status()
d=r.json(); o=d.get("result",d)
safe={"order_number":ORDER,"prr_option":o.get("prr_option")}
os.makedirs("dalli",exist_ok=True)
p="dalli/order_34598568-0351-1_prr_option.json"
open(p,"w",encoding="utf-8").write(json.dumps(safe,ensure_ascii=False,indent=2)+"\n")
print(json.dumps(safe,ensure_ascii=False,indent=2))
