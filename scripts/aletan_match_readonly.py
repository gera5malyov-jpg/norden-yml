#!/usr/bin/env python3
"""Read-only Aletan matching audit. Prints only public catalog/product metadata."""
from __future__ import annotations
import csv,io,os,re,sys,collections
from pathlib import Path
import requests

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"webasyst"))
from client import WebasystClient

TYPE_ID="202"
SOURCE="https://aletan.ru/upload/aletan_catalog_export.csv"

def norm(v):
    return re.sub(r"[^0-9a-zа-яё]+","",str(v or "").casefold())

def normalize_vendor(v):
    return str(v or "").strip().casefold().replace("ё","е")

def listify(p):
    if isinstance(p,list): return [x for x in p if isinstance(x,dict)]
    if not isinstance(p,dict): return []
    for field in ("products","items","data"):
        obj=p.get(field)
        if isinstance(obj,list):return [x for x in obj if isinstance(x,dict)]
        if isinstance(obj,dict):return [x for x in obj.values() if isinstance(x,dict)]
    return [x for x in p.values() if isinstance(x,dict)]

def variants(p):
    v=p.get("skus")
    return list(v.values()) if isinstance(v,dict) else v if isinstance(v,list) else []

def main():
    r=requests.get(SOURCE,timeout=100)
    r.raise_for_status()
    rows=list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig")),delimiter=";"))
    supplier=[x for x in rows if normalize_vendor(x.get("vendor"))=="алетан"]
    source_names=collections.defaultdict(set)
    source_codes=collections.Counter()
    for r in supplier:
        code=(r.get("vendorCode") or r.get("id") or "").strip()
        source_names[norm(r.get("name"))].add(code)
        source_codes[code]+=1
    wa=WebasystClient(min_request_interval=0.1)
    products=[]
    for offset in (0,1000,2000,3000):
        batch=listify(wa.call("shop.product.search",params={
            "hash":"type/"+TYPE_ID,"offset":offset,"limit":1000,"fields":"*,skus,stock_counts"}))
        products.extend(batch)
        if len(batch)<1000:break
    print("WA_TYPE_ALETAN_COUNT",len(products),"SOURCE",len(supplier),"SOURCE_UNIQUE_ARTICLES",len(source_codes))
    product_names=collections.Counter(norm(p.get("name")) for p in products)
    supplier_names=collections.Counter(norm(p.get("name")) for p in supplier)
    double_wa={name:n for name,n in product_names.items() if n>1}
    double_source={name:n for name,n in supplier_names.items() if n>1}
    eligible=sum(bool(product_names.get(name)==1 and supplier_names.get(name)==1) for name in supplier_names)
    print("NAME_MATCH_UNIQUENESS",{"wa_unique":len(product_names),"wa_ambiguous":len(double_wa),
        "source_unique":len(supplier_names),"source_ambiguous":len(double_source),
        "source_products_unique_on_both":eligible})
    print("NAME_DUPLICATE_SOURCE_EXAMPLES",
        [{"name":r.get("name"),"vendorCode":r.get("vendorCode")}
         for r in supplier if supplier_names[norm(r.get("name"))]>1][:12])
    counter=collections.Counter()
    samples=[]
    for p in products:
        skus=variants(p)
        codes={str(v.get("sku") or "") for v in skus if isinstance(v,dict)}
        matches_by_sku=codes & set(source_codes)
        n=norm(p.get("name"))
        by_name=source_names.get(n,set())
        if matches_by_sku:counter["match_sku"]+=1
        if by_name:counter["match_name"]+=1
        if codes and all(x.startswith("AF-") for x in codes):counter["all_internal_af"]+=1
        if not matches_by_sku and not by_name:counter["neither"]+=1
        if len(samples)<15 and (matches_by_sku or by_name or len(samples)<4):
            samples.append({
                "id":p.get("id"),"name":str(p.get("name") or "")[:100],
                "sku":list(codes)[:2],
                "name_matches":list(by_name)[:2],
                "sku_matches":list(matches_by_sku)[:2],
                "other_meta_keys":[k for k in p if "external" in k.lower() or "yml" in k.lower()],
                "modified":p.get("edit_datetime")})
    meta=collections.Counter()
    group=collections.Counter()
    samples_meta=[]
    wrong_meta=[]
    for p in products:
        ext=str(p.get("yml_id") or "").strip()
        profile=str(p.get("yml_profile_id") or "").strip()
        group[profile or "<empty>"]+=1
        if ext:meta["yml_id_set"]+=1
        if profile:meta["profile_set"]+=1
        if ext and ext in source_codes:meta["yml_id_matches_raw_vendorCode"]+=1
        if ext and ext not in source_codes:meta["yml_id_not_raw_code"]+=1
        if not ext:meta["yml_id_empty"]+=1
        if profile and ext and len(samples_meta)<15:
            samples_meta.append({"product_id":p.get("id"),"yml_id":ext,
                                 "yml_profile_id":profile,
                                 "sku":[x.get("sku") for x in variants(p) if isinstance(x,dict)][:2],
                                 "modified":p.get("edit_datetime"),
                                 "updated":p.get("yml_updated")})
        if not ext and len(wrong_meta)<5:
            wrong_meta.append({"product_id":p.get("id"),"name":p.get("name"),
                               "sku":[x.get("sku") for x in variants(p) if isinstance(x,dict)][:2],
                               "yml_profile_id":p.get("yml_profile_id")})
    focus_codes={"B1001","B102","B1002","B307"}
    focus=[]
    for p in products:
        vskus=variants(p)
        name=norm(p.get("name"))
        linked=source_names.get(name,set())
        if linked.intersection(focus_codes) or str(p.get("id")) in ("1474987","1443656","1476508"):
            focus.append({
                "id":p.get("id"),"name":p.get("name"),"sku":[x.get("sku") for x in vskus if isinstance(x,dict)],
                "sourceCodes":list(linked)[:5],"yml_id":p.get("yml_id"),
                "yml_profile_id":p.get("yml_profile_id"),"yml_updated":p.get("yml_updated"),
                "type_id":p.get("type_id"),"edit_datetime":p.get("edit_datetime"),
                "status":p.get("status"),"id_1c":p.get("id_1c")})
    print("B1001_FOCUS",focus[:18])
    print("YML_META_COUNTS",dict(meta),"PROFILE_GROUPS",dict(group))
    print("YML_META_SAMPLES",samples_meta)
    print("YML_MISSING_SAMPLES",wrong_meta)
    print("COUNTS",dict(counter))
    print("SAMPLE",samples)
    if products:
        product_id=str(products[0].get("id"))
        try:
            record=wa.call("shop.product.getInfo",params={"id":product_id})
            print("DETAIL_GET_INFO_TYPE",type(record).__name__,
                  "DETAIL_KEYS",list(record)[:60] if isinstance(record,dict) else "not dict")
        except Exception as e:
            print("DETAIL_GET_INFO_ERROR",str(e)[:250])
    print("READ_ONLY_COMPLETE")

if __name__=="__main__":main()
