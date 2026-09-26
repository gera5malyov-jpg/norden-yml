#!/usr/bin/env python3
from __future__ import annotations
import json, os, re, time, requests, importlib.util
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def load(path,name):
    p=ROOT/path
    spec=importlib.util.spec_from_file_location(name,p)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

BRIDGE=load(Path("norden-kit")/"webasyst_n100_to_kit_once.py","kit_bridge")
MOD=load(Path("norden-kit")/"sync_norden_kit.py","kit_sync")

OZON_BASE="https://api-seller.ozon.ru"

def s(v): return str(v or "").strip()
def norm(v): return re.sub(r"\s+"," ",s(v)).casefold().strip()

class OzonClient:
    def __init__(self):
        self.s=requests.Session()
        self.h={
            "Client-Id":os.environ["OZON_CLIENT_ID"],
            "Api-Key":os.environ["OZON_API_KEY"],
            "Content-Type":"application/json",
            "Accept":"application/json",
        }
    def post(self,path,body):
        r=self.s.post(OZON_BASE+path,headers=self.h,json=body,timeout=120)
        if not r.ok:
            raise RuntimeError(f"{path}: HTTP {r.status_code}: {r.text[:1500]}")
        return r.json() if r.content else {}

def exact_ozon_product(oz,offer_id):
    d=oz.post("/v3/product/list",{"filter":{"offer_id":[offer_id],"visibility":"ALL"},"limit":100})
    items=((d.get("result") or {}).get("items") or [])
    exact=[x for x in items if s(x.get("offer_id"))==offer_id]
    if len(exact)!=1:
        raise RuntimeError(f"Ozon exact offer_id {offer_id}: found {len(exact)}")
    pid=int(exact[0].get("product_id") or 0)
    if not pid:
        raise RuntimeError(f"Ozon exact offer_id {offer_id}: product_id missing")
    return pid,exact[0]

def ozon_readback(oz,offer_id):
    pid,list_row=exact_ozon_product(oz,offer_id)
    attrs=oz.post("/v4/product/info/attributes",{"filter":{"product_id":[pid],"visibility":"ALL"},"limit":1000})
    rows=attrs.get("result") or []
    exact=[x for x in rows if int(x.get("id") or x.get("product_id") or 0)==pid and s(x.get("offer_id"))==offer_id]
    if len(exact)!=1:
        raise RuntimeError(f"Ozon attributes readback {offer_id}: found {len(exact)}")
    card=exact[0]
    info=oz.post("/v3/product/info/list",{"product_id":[pid]})
    infos=info.get("items") or (info.get("result") or {}).get("items") or []
    exact_info=[x for x in infos if int(x.get("id") or x.get("product_id") or 0)==pid and s(x.get("offer_id"))==offer_id]
    if len(exact_info)!=1:
        raise RuntimeError(f"Ozon info readback {offer_id}: found {len(exact_info)}")
    info_row=exact_info[0]
    st=info_row.get("statuses") or {}
    created=bool(st.get("is_created")) or bool(info_row.get("id"))
    if not created:
        raise RuntimeError(f"Ozon {offer_id}: card not confirmed created")
    return {"product_id":pid,"list":list_row,"attributes":card,"info":info_row}

def attribute_schema(oz,description_category_id,type_id):
    d=oz.post("/v1/description-category/attribute",{
        "description_category_id":int(description_category_id),
        "type_id":int(type_id),
        "language":"DEFAULT",
    })
    return {int(x.get("id") or 0):x for x in (d.get("result") or []) if int(x.get("id") or 0)}

def exact_kit_variant(kit,offer_id):
    payload=kit.request("GET","/v1/variants",params={"name":offer_id,"page":1,"per_page":100})
    rows=kit.items(payload)
    exact=[x for x in rows if s(x.get("sku"))==offer_id]
    if len(exact)!=1:
        raise RuntimeError(f"KIT exact SKU {offer_id}: found {len(exact)}")
    vid=s(exact[0].get("id"))
    if not vid: raise RuntimeError(f"KIT {offer_id}: variant id missing")
    return vid

def characteristic_index(kit):
    rows=kit.characteristics()
    by_title=defaultdict(list)
    for x in rows:
        if s(x.get("title")):
            by_title[MOD.norm_title(x["title"])].append(x)
    return rows,by_title

def characteristic_id(kit,rows,by_title,title):
    return MOD.characteristic_id(kit,rows,by_title,title)

def is_ozon_characteristic_title(title):
    return s(title).startswith("Ozon ")

def join_values(values):
    out=[]
    ids=[]
    for v in values or []:
        if not isinstance(v,dict): continue
        val=s(v.get("value"))
        if val: out.append(val)
        did=v.get("dictionary_value_id")
        if did not in (None,"",0,"0"):
            ids.append(str(did))
    return ";".join(out),ids

def build_ozon_rows(kit,rows,by_title,card,info,schema):
    result=[]
    def add(title,value):
        if value is None or s(value)=="":
            return
        cid=characteristic_id(kit,rows,by_title,title)
        sv=s(value)
        result.append({"characteristic_id":cid,"value":sv,"values":[sv]})

    pid=int(card.get("id") or info.get("id") or 0)
    offer=s(card.get("offer_id") or info.get("offer_id"))
    sku=s(card.get("sku") or info.get("sku"))
    dc=card.get("description_category_id") or info.get("description_category_id")
    tid=card.get("type_id") or info.get("type_id")

    add("Ozon 0 — source_product_id",pid)
    add("Ozon 0 — source_offer_id",offer)
    add("Ozon 0 — source_sku",sku)
    add("Ozon 0 — description_category_id",dc)
    add("Ozon 0 — type_id",tid)
    add("Ozon 0 — readback_at",time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()))
    add("Ozon 0 — Статус данных","КАНОНИЧЕСКИЕ ДАННЫЕ ИЗ OZON")

    # Top-level facts returned by Ozon but not ordinary attributes.
    add("Ozon 0 — Фактическое название Ozon",card.get("name") or info.get("name"))
    add("Ozon 0 — Ozon height",card.get("height"))
    add("Ozon 0 — Ozon width",card.get("width"))
    add("Ozon 0 — Ozon depth",card.get("depth"))
    add("Ozon 0 — Ozon dimension_unit",card.get("dimension_unit"))
    add("Ozon 0 — Ozon weight",card.get("weight"))
    add("Ozon 0 — Ozon weight_unit",card.get("weight_unit"))
    status=info.get("statuses") or {}
    add("Ozon 0 — status",status.get("status"))
    add("Ozon 0 — status_name",status.get("status_name"))
    add("Ozon 0 — moderate_status",status.get("moderate_status"))
    add("Ozon 0 — validation_status",status.get("validation_status"))

    for a in card.get("attributes") or []:
        aid=int(a.get("id") or a.get("attribute_id") or 0)
        if not aid: continue
        meta=schema.get(aid) or {}
        name=s(meta.get("name")) or f"attribute_{aid}"
        value,dict_ids=join_values(a.get("values") or [])
        if value:
            add(f"Ozon {aid} — {name}",value)
        if dict_ids:
            add(f"Ozon {aid} — dictionary_value_id",";".join(dict_ids))
    return result

def sync_offer_to_kit(offer_id, expected_kit_variant_id=None):
    oz=OzonClient(); kit=BRIDGE.KitClient()
    rb=ozon_readback(oz,offer_id)
    card=rb["attributes"]; info=rb["info"]
    dc=card.get("description_category_id") or info.get("description_category_id")
    tid=card.get("type_id") or info.get("type_id")
    if not dc or not tid:
        raise RuntimeError(f"Ozon {offer_id}: description_category_id/type_id missing")
    schema=attribute_schema(oz,dc,tid)

    vid=exact_kit_variant(kit,offer_id)
    if expected_kit_variant_id and vid!=expected_kit_variant_id:
        raise RuntimeError(f"KIT {offer_id}: variant mismatch expected {expected_kit_variant_id}, got {vid}")
    current=kit.request("GET",f"/v1/variants/{vid}")
    rows,by_title=characteristic_index(kit)
    char_meta={s(x.get("id")):s(x.get("title")) for x in rows}

    keep=[]
    removed=[]
    for c in current.get("characteristics") or []:
        title=char_meta.get(s(c.get("characteristic_id")),"")
        if is_ozon_characteristic_title(title):
            removed.append({"title":title,"value":c.get("value")})
        else:
            keep.append(c)

    canonical=build_ozon_rows(kit,rows,by_title,card,info,schema)
    patch={"characteristics":keep+canonical}
    kit.patch_variant(vid,patch)

    verify=kit.request("GET",f"/v1/variants/{vid}")
    rows2=kit.characteristics()
    titles2={s(x.get("id")):s(x.get("title")) for x in rows2}
    ozon_after=[]
    for c in verify.get("characteristics") or []:
        title=titles2.get(s(c.get("characteristic_id")),"")
        if is_ozon_characteristic_title(title):
            ozon_after.append({"title":title,"value":s(c.get("value"))})
    if not any(x["title"]=="Ozon 0 — Статус данных" and x["value"]=="КАНОНИЧЕСКИЕ ДАННЫЕ ИЗ OZON" for x in ozon_after):
        raise RuntimeError(f"KIT {offer_id}: canonical Ozon status missing after readback")
    return {
        "offer_id":offer_id,"ozon_product_id":rb["product_id"],"kit_variant_id":vid,
        "removed_temporary_count":len(removed),
        "canonical_count":len(ozon_after),
        "status":"КАНОНИЧЕСКИЕ ДАННЫЕ ИЗ OZON",
    }

def main():
    offer=s(os.environ.get("OZON_POSTPUBLISH_OFFER_ID"))
    if not offer:
        raise RuntimeError("OZON_POSTPUBLISH_OFFER_ID is required")
    expected=s(os.environ.get("KIT_VARIANT_ID"))
    result=sync_offer_to_kit(offer,expected or None)
    out=ROOT/"catalog"/"ozon_postpublish_to_kit_report.json"
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))

if __name__=="__main__":
    main()
