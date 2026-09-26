#!/usr/bin/env python3
import json, os, requests
from pathlib import Path
OUT=Path("catalog/yandex_chair_parameters_probe.json")
TOKEN=os.environ["YANDEX_MARKET_API_KEY"]
BASE="https://api.partner.market.yandex.ru"
h={"Api-Key":TOKEN,"Content-Type":"application/json","Accept":"application/json"}
cats=[61276996,10785222]
out={}
for cid in cats:
    r=requests.post(BASE+f"/v2/category/{cid}/parameters",headers=h,json={"language":"RU"},timeout=120)
    out[str(cid)]={"status":r.status_code}
    try: out[str(cid)]["json"]=r.json()
    except: out[str(cid)]["text"]=r.text[:5000]
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({k:v["status"] for k,v in out.items()},ensure_ascii=False))
