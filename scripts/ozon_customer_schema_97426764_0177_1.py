#!/usr/bin/env python3
import json, os, requests

order_no=os.environ["TARGET_EXTERNAL_ID"].strip()
cid=os.environ["OZON_CLIENT_ID"].strip()
key=os.environ["OZON_API_KEY"].strip()
out=os.environ.get("SAFE_RESULT_PATH","ozon/order_97426764-0177-1_customer_schema.json")
r=requests.post(
    "https://api-seller.ozon.ru/v3/posting/fbs/get",
    headers={"Client-Id":cid,"Api-Key":key,"Content-Type":"application/json"},
    json={"posting_number":order_no,"with":{"analytics_data":False,"barcodes":False,"financial_data":False,"translit":False}},
    timeout=60
)
r.raise_for_status()
d=r.json()
o=d.get("result") if isinstance(d,dict) and isinstance(d.get("result"),dict) else d
c=o.get("customer") if isinstance(o.get("customer"),dict) else {}
a=c.get("address") if isinstance(c.get("address"),dict) else {}

def shape(obj):
    out={}
    for k,v in sorted(obj.items()):
        if isinstance(v,dict):
            out[k]={"type":"dict","keys":sorted(v.keys())}
        elif isinstance(v,list):
            out[k]={"type":"list","count":len(v)}
        elif isinstance(v,str):
            out[k]={"type":"str","present":bool(v.strip()),"length":len(v.strip())}
        else:
            out[k]={"type":type(v).__name__,"present":v is not None}
    return out

res={
    "order_number":order_no,
    "customer":shape(c),
    "address":shape(a),
    "top_level_keys":sorted(o.keys()),
}
os.makedirs(os.path.dirname(out),exist_ok=True)
with open(out,"w",encoding="utf-8") as f:
    json.dump(res,f,ensure_ascii=False,indent=2)
    f.write("\n")
print(json.dumps(res,ensure_ascii=False,indent=2))
