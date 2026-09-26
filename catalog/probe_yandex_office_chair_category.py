#!/usr/bin/env python3
import json, os, requests
from pathlib import Path
OUT=Path("catalog/yandex_office_chair_category_probe.json")
TOKEN=os.environ["YANDEX_MARKET_API_KEY"]
BASE="https://api.partner.market.yandex.ru"
h={"Api-Key":TOKEN,"Content-Type":"application/json","Accept":"application/json"}
r=requests.post(BASE+"/v2/categories/tree",headers=h,json={"language":"RU"},timeout=120)
r.raise_for_status()
root=(r.json() or {}).get("result") or {}
hits=[]
def walk(n,path):
    if not isinstance(n,dict): return
    name=str(n.get("name") or "")
    p=path+([name] if name else [])
    children=[x for x in (n.get("children") or []) if isinstance(x,dict)]
    if n.get("id") and not children:
        text=" > ".join(p)
        low=text.casefold()
        if ("крес" in low and "офис" in low) or ("стул" in low and ("офис" in low or "мебел" in low)):
            hits.append({"id":n.get("id"),"name":name,"path":text})
    for x in children: walk(x,p)
walk(root,[])
OUT.write_text(json.dumps(hits,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(hits,ensure_ascii=False))
