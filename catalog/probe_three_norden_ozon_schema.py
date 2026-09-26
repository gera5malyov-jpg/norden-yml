#!/usr/bin/env python3
from __future__ import annotations
import json, os, requests, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"catalog"/"three_new_norden_source_and_ozon_schema.json"
skus=["CK38F","B1816 3S fabric LE8100-07","CK-2518A-P"]
report={"norden":{},"ozon":{}}

# Official Norden API
secret=os.environ["NORDEN_SECRET"]
for sku in skus:
    r=requests.get("https://norden.group/api-products/",headers={"secret":secret,"Accept":"application/json"},params={"sku":sku},timeout=120)
    r.raise_for_status()
    rows=(r.json() or {}).get("products") or []
    exact=[p for p in rows if str(p.get("product_code") or "").strip()==sku]
    report["norden"][sku]={"matches":len(exact),"product":exact[0] if len(exact)==1 else None}

# Ozon category tree + office chair attributes
headers={
  "Client-Id":os.environ["OZON_CLIENT_ID"],
  "Api-Key":os.environ["OZON_API_KEY"],
  "Content-Type":"application/json",
  "Accept":"application/json",
}
sess=requests.Session()
def post(path,body):
    rr=sess.post("https://api-seller.ozon.ru"+path,headers=headers,json=body,timeout=120)
    return {"status":rr.status_code,"text":rr.text[:5000],"json":(rr.json() if rr.ok and rr.content else None)}

tree=post("/v1/description-category/tree",{"language":"DEFAULT"})
report["ozon"]["tree_status"]=tree["status"]
report["ozon"]["tree_error"]=None if tree["status"]<400 else tree["text"]
candidates=[]
if tree.get("json"):
    def walk(node,path,inherited_dc=None):
        if isinstance(node,dict):
            name=str(node.get("category_name") or node.get("name") or node.get("type_name") or "")
            p=path+([name] if name else [])
            dc=node.get("description_category_id") or node.get("category_id") or inherited_dc
            if node.get("type_id") is not None:
                candidates.append({
                  "description_category_id":dc,
                  "type_id":node.get("type_id"),
                  "name":name,
                  "path":p,
                  "raw":node,
                })
            for key in ("children","types","items","result"):
                val=node.get(key)
                if isinstance(val,list):
                    for x in val: walk(x,p,dc)
                elif isinstance(val,dict): walk(val,p,dc)
        elif isinstance(node,list):
            for x in node: walk(x,path,inherited_dc)
    walk(tree["json"],[],None)
matches=[x for x in candidates if "офис" in (" ".join(x["path"])).casefold() and "крес" in (" ".join(x["path"])).casefold()]
report["ozon"]["office_chair_candidates"]=matches[:50]
attrs=[]
for cand in matches[:10]:
    dc=cand.get("description_category_id"); tid=cand.get("type_id")
    if not dc or not tid: continue
    res=post("/v1/description-category/attribute",{"description_category_id":dc,"type_id":tid,"language":"DEFAULT"})
    attrs.append({"candidate":cand,"response":res})
report["ozon"]["attribute_attempts"]=attrs
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"norden":{k:v["matches"] for k,v in report["norden"].items()},"tree_status":tree["status"],"candidates":len(matches),"attr_statuses":[x["response"]["status"] for x in attrs]},ensure_ascii=False))
