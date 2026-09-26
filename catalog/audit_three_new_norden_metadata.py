#!/usr/bin/env python3
from __future__ import annotations
import json, os, importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"catalog"/"audit_three_new_norden_metadata.json"

def load(path,name):
    p=ROOT/path
    spec=importlib.util.spec_from_file_location(name,p)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

B=load(Path("norden-kit")/"webasyst_n100_to_kit_once.py","bridge_meta")
kit=B.KitClient()
cats=kit.categories()
chars=kit.characteristics()
cat_by={str(x.get("id") or ""):x for x in cats}
char_by={str(x.get("id") or ""):x for x in chars}

def path(cid):
    cur=str(cid or ""); out=[]; seen=set()
    while cur and cur not in seen and cur in cat_by:
        seen.add(cur); r=cat_by[cur]
        out.append(str(r.get("title") or r.get("name") or ""))
        cur=str(r.get("parent_id") or "")
    return list(reversed([x for x in out if x]))

variants=[
 "01a0dfaa-3c5e-7939-9898-c89cc10b6b53",
 "01a0df2f-3030-7957-a8f4-1561c0d7ca95",
 "01a0df2f-eb73-7572-b95a-a3fef52bec7f",
]
res=[]
for vid in variants:
    v=kit.request("GET",f"/v1/variants/{vid}")
    p=kit.request("GET",f"/v1/products/{v.get('product_id')}")
    mapped=[]
    for cv in (v.get("characteristics") or []):
        cid=str(cv.get("characteristic_id") or "")
        meta=char_by.get(cid,{})
        mapped.append({
          "id":cid,
          "title":str(meta.get("title") or meta.get("name") or ""),
          "value":cv.get("value"),
          "values":cv.get("values"),
        })
    cids=p.get("category_ids") or []
    res.append({
      "sku":v.get("sku"),
      "kit_id":v.get("kit_id"),
      "cargo_boxes":v.get("cargo_boxes") or [],
      "category_ids":cids,
      "category_paths":[path(x) for x in cids],
      "characteristics":mapped
    })
OUT.write_text(json.dumps(res,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(res,ensure_ascii=False,indent=2))
