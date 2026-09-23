#!/usr/bin/env python3
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webasyst"))
from client import WebasystClient

def s(v): return str(v or "").strip()
def listify(payload):
    if isinstance(payload, list): return payload
    if isinstance(payload, dict):
        for k in ("products","items"):
            if isinstance(payload.get(k), list): return payload[k]
        if payload and all(isinstance(v,dict) for v in payload.values()): return list(payload.values())
    return []

def skus(prod):
    x=prod.get("skus") or {}
    return list(x.values()) if isinstance(x,dict) else x if isinstance(x,list) else []

wa=WebasystClient(min_request_interval=0.1)
products=[]
offset=0
while True:
    p=wa.call("shop.product.search",params={"hash":"search/query=RED-","offset":offset,"limit":1000,"fields":"id,name,summary,skus"})
    batch=listify(p)
    products.extend(batch)
    total=p.get("count") if isinstance(p,dict) else None
    if not batch or len(batch)<1000 or (total is not None and len(products)>=int(total)): break
    offset+=len(batch)

managed=[]
for prod in products:
    red=[s(x.get("sku")) for x in skus(prod) if s(x.get("sku")).upper().startswith("RED-")]
    if red:
        managed.append((prod,red))

correct=0
incorrect=0
no_summary=0
samples=[]
for prod,red in managed:
    summary=s(prod.get("summary"))
    urls=re.findall(r"https?://[^\s\[\]<>]+", summary)
    blocks=re.findall(r"\[extimg\]\s*(https?://[^\s\[\]<>]+)\s*\[/extimg\]", summary, flags=re.I)
    ok=bool(urls) and len(blocks)==len(urls) and blocks==urls
    if ok: correct+=1
    else:
        incorrect+=1
        if not summary: no_summary+=1
        if len(samples)<20:
            samples.append({"sku":red[0],"urls":len(urls),"blocks":len(blocks),"summary":summary[:300]})

print(json.dumps({
    "search_products":len(products),
    "red_products":len(managed),
    "correct_extimg_per_image":correct,
    "incorrect_extimg":incorrect,
    "no_summary":no_summary,
    "sample_incorrect":samples
},ensure_ascii=False,indent=2))
