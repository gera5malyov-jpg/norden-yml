#!/usr/bin/env python3
import json, os, requests
sku="B1816 3S fabric LE8100-07"
secret=os.environ["NORDEN_SECRET"]
r=requests.get("https://norden.group/api-products/",headers={"secret":secret,"Accept":"application/json"},params={"sku":sku},timeout=120)
r.raise_for_status()
data=r.json()
rows=data.get("products") or []
out={"sku":sku,"count":len(rows),"products":[]}
for p in rows:
    imgs=[str(x).strip() for x in (p.get("images") or []) if str(x).strip()]
    checks=[]
    for u in imgs:
        try:
            rr=requests.get(u,allow_redirects=True,timeout=30,headers={"User-Agent":"Mozilla/5.0"})
            checks.append({"url":u,"status":rr.status_code,"final_url":rr.url,"content_type":rr.headers.get("Content-Type"),"bytes":len(rr.content)})
        except Exception as e:
            checks.append({"url":u,"error":str(e)[:500]})
    out["products"].append({
        "product_code":p.get("product_code"),
        "Kod":p.get("Kod"),
        "name":p.get("name"),
        "images":imgs,
        "checks":checks,
    })
open("catalog/norden_api_corfu_images.json","w",encoding="utf-8").write(json.dumps(out,ensure_ascii=False,indent=2))
print(json.dumps(out,ensure_ascii=False,indent=2))
