#!/usr/bin/env python3
import json, os, requests
from pathlib import Path
OUT=Path("catalog/ozon_postpublish_shape_probe.json")
headers={"Client-Id":os.environ["OZON_CLIENT_ID"],"Api-Key":os.environ["OZON_API_KEY"],"Content-Type":"application/json","Accept":"application/json"}
s=requests.Session()
def post(path,body):
 r=s.post("https://api-seller.ozon.ru"+path,headers=headers,json=body,timeout=120)
 out={"status":r.status_code}
 try: out["json"]=r.json()
 except: out["text"]=r.text[:5000]
 return out
offer="AF-31646769"
lst=post("/v3/product/list",{"filter":{"offer_id":[offer],"visibility":"ALL"},"limit":100})
items=((lst.get("json") or {}).get("result") or {}).get("items") or []
pids=[int(x.get("product_id") or 0) for x in items if int(x.get("product_id") or 0)]
attrs=post("/v4/product/info/attributes",{"filter":{"product_id":pids,"visibility":"ALL"},"limit":1000}) if pids else {}
info=post("/v3/product/info/list",{"product_id":pids}) if pids else {}
OUT.write_text(json.dumps({"offer":offer,"list":lst,"attrs":attrs,"info":info},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"offer":offer,"pids":pids,"list_status":lst.get("status"),"attrs_status":attrs.get("status"),"info_status":info.get("status")},ensure_ascii=False))
