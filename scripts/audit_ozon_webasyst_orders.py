#!/usr/bin/env python3
import json, os
from collections import Counter
from datetime import datetime, timedelta, timezone
import requests

OZON_CLIENT_ID=os.getenv("OZON_CLIENT_ID","").strip()
OZON_API_KEY=os.getenv("OZON_API_KEY","").strip()
WA_TOKEN=os.getenv("WEBASYST_API_TOKEN","").strip()
WA_BASE=os.getenv("WEBASYST_BASE_URL","https://profikompany.ru").rstrip("/")

out={
  "generated_at": datetime.now(timezone.utc).isoformat(),
  "ozon": {},
  "webasyst": {},
  "read_only": True,
  "contains_pii": False
}

def ozon_post(path, body):
    if not OZON_CLIENT_ID or not OZON_API_KEY:
        return {"ok":False,"error":"Ozon credentials missing"}
    r=requests.post("https://api-seller.ozon.ru"+path,
        headers={"Client-Id":OZON_CLIENT_ID,"Api-Key":OZON_API_KEY,"Content-Type":"application/json"},
        json=body, timeout=60)
    try: data=r.json()
    except Exception: data={"raw":r.text[:500]}
    return {"ok":r.ok,"http":r.status_code,"data":data}

now=datetime.now(timezone.utc)
since=now-timedelta(days=14)
iso=lambda d:d.strftime("%Y-%m-%dT%H:%M:%SZ")

fbs=ozon_post("/v3/posting/fbs/list",{
  "dir":"DESC",
  "filter":{"since":iso(since),"to":iso(now)},
  "limit":1000,
  "offset":0,
  "with":{"analytics_data":False,"barcodes":False,"financial_data":False,"translit":False}
})
if fbs.get("ok"):
    postings=(fbs["data"].get("result") or {}).get("postings") or []
    out["ozon"]["fbs"]={
      "ok":True,
      "count_sample":len(postings),
      "statuses":dict(sorted(Counter(str(x.get("status","")) for x in postings).items())),
      "substatuses":dict(sorted(Counter(str(x.get("substatus","")) for x in postings if x.get("substatus")).items())),
      "has_products": any(bool(x.get("products")) for x in postings),
      "sample_product_fields": sorted(list((postings[0].get("products") or [{}])[0].keys())) if postings and postings[0].get("products") else []
    }
else:
    out["ozon"]["fbs"]={"ok":False,"http":fbs.get("http"),"error":str(fbs.get("data"))[:1000]}

fbo=ozon_post("/v2/posting/fbo/list",{
  "dir":"DESC",
  "filter":{"since":iso(since),"to":iso(now)},
  "limit":1000,
  "offset":0,
  "translit":False,
  "with":{"analytics_data":False,"financial_data":False}
})
if fbo.get("ok"):
    postings=(fbo["data"].get("result") or [])
    if isinstance(postings,dict): postings=postings.get("postings") or postings.get("result") or []
    out["ozon"]["fbo"]={
      "ok":True,
      "count_sample":len(postings) if isinstance(postings,list) else 0,
      "statuses":dict(sorted(Counter(str(x.get("status","")) for x in postings).items())) if isinstance(postings,list) else {},
      "has_products": any(bool(x.get("products")) for x in postings) if isinstance(postings,list) else False
    }
else:
    out["ozon"]["fbo"]={"ok":False,"http":fbo.get("http"),"error":str(fbo.get("data"))[:1000]}

def wa_get(method):
    if not WA_TOKEN:
        return {"ok":False,"error":"Webasyst token missing"}
    r=requests.get(f"{WA_BASE}/api.php/{method}",params={"format":"json","access_token":WA_TOKEN},timeout=60)
    try: data=r.json()
    except Exception: data={"raw":r.text[:500]}
    return {"ok":r.ok and not (isinstance(data,dict) and data.get("error")),"http":r.status_code,"data":data}

settings=wa_get("shop.settings.get")
if settings.get("ok"):
    data=settings["data"]
    states=data.get("order_states") if isinstance(data,dict) else None
    if isinstance(states,dict):
        normalized={k:{"name":(v.get("name") if isinstance(v,dict) else str(v))} for k,v in states.items()}
    elif isinstance(states,list):
        normalized=[{"id":x.get("id"),"name":x.get("name")} if isinstance(x,dict) else str(x) for x in states]
    else:
        normalized=states
    out["webasyst"]={"ok":True,"order_states":normalized}
else:
    out["webasyst"]={"ok":False,"http":settings.get("http"),"error":str(settings.get("data"))[:1000]}

os.makedirs("ozon",exist_ok=True)
with open("ozon/order_sync_audit.json","w",encoding="utf-8") as f:
    json.dump(out,f,ensure_ascii=False,indent=2)
print(json.dumps(out,ensure_ascii=False,indent=2))
