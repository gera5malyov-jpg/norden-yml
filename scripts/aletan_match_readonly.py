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
