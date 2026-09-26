#!/usr/bin/env python3
from __future__ import annotations
import json, os, sys, importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"catalog"/"audit_three_new_norden_products.json"

def load(path,name):
    p=ROOT/path
    spec=importlib.util.spec_from_file_location(name,p)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

os.environ.setdefault("CATALOG_SPREADSHEET_ID","1DvMYvKRr8fX8mfLFaeJCXCccQofPViPcIP2CgIctc8w")
os.environ.setdefault("GOOGLE_SERVICE_ACCOUNT_JSON","{}")
SYNC=load(Path("catalog")/"sync_norden_chairs_stools_6h.py","audit_sync")
BRIDGE=SYNC.BRIDGE
sys.path.insert(0,str(ROOT/"webasyst"))
from client import WebasystClient

items=[
 {"article":"AF-31662372","yml":"CK38F","wa_pid":"1483234"},
 {"article":"AF-31662420","yml":"B1816 3S fabric LE8100-07","wa_pid":"1483282"},
 {"article":"AF-31662421","yml":"CK-2518A-P","wa_pid":"1483283"},
]

wa=WebasystClient(min_request_interval=0.45)
kit=BRIDGE.KitClient()

# Resolve Webasyst type names
types=SYNC.listify(wa.call("shop.type.getList"))
type_names={str(x.get("id")):str(x.get("name") or x.get("title") or "") for x in types}

# KIT warehouses and categories
warehouses={str(x.get("id")):str(x.get("title") or x.get("name") or "") for x in kit.warehouses()}
categories={str(x.get("id")):x for x in kit.categories()}

def cat_path(cid):
    out=[]; seen=set(); cur=str(cid or "")
    while cur and cur not in seen and cur in categories:
        seen.add(cur); row=categories[cur]
        out.append(str(row.get("title") or row.get("name") or ""))
        cur=str(row.get("parent_id") or "")
    return list(reversed([x for x in out if x]))

def exact_kit(article):
    payload=kit.request("GET","/v1/variants",params={"name":article,"page":1,"per_page":100})
    rows=kit.items(payload)
    return [x for x in rows if str(x.get("sku") or "").strip()==article]

def kit_urls(variant):
    out=[]
    media=[m for m in (variant.get("media") or []) if isinstance(m,dict) and str(m.get("type") or "").upper()=="IMAGE"]
    media.sort(key=lambda m:int(m.get("display_sequence") or 0))
    for m in media:
        iid=str(m.get("image_id") or "").strip()
        if not iid: continue
        try:
            meta=kit.request("GET",f"/v1/files/{iid}")
            out.append({"image_id":iid,"url":str(meta.get("url") or "")})
        except Exception as e:
            out.append({"image_id":iid,"error":str(e)[:500]})
    return out

report={"ok":True,"items":[]}
for item in items:
    row={"article":item["article"],"expected_yml":item["yml"],"webasyst":{},"kit":{}}
    try:
        info=wa.call("shop.product.getInfo",params={"id":item["wa_pid"]})
        skus=SYNC.listify(wa.call("shop.product.skus.getList",params={"product_id":item["wa_pid"]}),("skus","items"))
        exact=[s for s in skus if str(s.get("sku") or "").strip()==item["article"]]
        row["webasyst"]={
          "product_id":str(info.get("id") or item["wa_pid"]) if isinstance(info,dict) else item["wa_pid"],
          "name":str(info.get("name") or "") if isinstance(info,dict) else "",
          "type_id":str(info.get("type_id") or "") if isinstance(info,dict) else "",
          "type_name":type_names.get(str(info.get("type_id") or ""),"") if isinstance(info,dict) else "",
          "yml_id":str(info.get("yml_id") or "") if isinstance(info,dict) else "",
          "url":str(info.get("frontend_url") or info.get("url") or "") if isinstance(info,dict) else "",
          "summary":str(info.get("summary") or "") if isinstance(info,dict) else "",
          "description":str(info.get("description") or "") if isinstance(info,dict) else "",
          "features":info.get("features") if isinstance(info,dict) else None,
          "sku_matches":len(exact),
          "sku":exact[0] if len(exact)==1 else None,
        }
    except Exception as e:
        row["webasyst"]={"error":str(e)[:1200]}
        report["ok"]=False

    try:
        hits=exact_kit(item["article"])
        row["kit"]["exact_sku_matches"]=len(hits)
        if len(hits)==1:
            vid=str(hits[0].get("id") or "")
            full=kit.request("GET",f"/v1/variants/{vid}")
            pid=str(full.get("product_id") or hits[0].get("product_id") or "")
            prod=kit.request("GET",f"/v1/products/{pid}") if pid else {}
            stocks=[]
            raw=full.get("stocks") or []
            if isinstance(raw,list):
                for s in raw:
                    if isinstance(s,dict):
                        wid=str(s.get("warehouse_id") or "")
                        stocks.append({"warehouse_id":wid,"warehouse":warehouses.get(wid,""),"quantity":s.get("quantity")})
            elif isinstance(raw,dict):
                for wid,s in raw.items():
                    qty=s.get("quantity") if isinstance(s,dict) else s
                    stocks.append({"warehouse_id":str(wid),"warehouse":warehouses.get(str(wid),""),"quantity":qty})
            row["kit"].update({
              "variant_id":vid,
              "product_id":pid,
              "kit_id":full.get("kit_id"),
              "sku":str(full.get("sku") or ""),
              "name":str(full.get("name") or ""),
              "brand":full.get("brand"),
              "status":full.get("status"),
              "pricing":full.get("pricing"),
              "stocks":stocks,
              "category_id":str(prod.get("category_id") or "") if isinstance(prod,dict) else "",
              "category_path":cat_path(prod.get("category_id")) if isinstance(prod,dict) else [],
              "media":full.get("media") or [],
              "public_images":kit_urls(full),
              "characteristics":full.get("characteristics") or full.get("characteristic_values") or [],
              "raw_keys":sorted(full.keys()) if isinstance(full,dict) else [],
            })
    except Exception as e:
        row["kit"]["error"]=str(e)[:1200]
        report["ok"]=False
    report["items"].append(row)

OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(report,ensure_ascii=False,indent=2))
