#!/usr/bin/env python3
"""Strict read-only Webasyst-Aletan match plan for restoring YML Pro bindings."""
from __future__ import annotations
import collections
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"webasyst"))
from client import WebasystClient

ROOT=Path(__file__).resolve().parents[1]
FEED=ROOT/"aletan-yml/aletan.yml"
OUTPUT=ROOT/"aletan_ymlpro_plan.tmp.json"
PROFILE=373
TYPE=202

def norm(v):
    v=unicodedata.normalize("NFKC",str(v or "")).casefold()
    return re.sub(r"[^0-9a-zа-яё]+","",v)

def trim(v):
    return str(v or "").strip()

def products_list(payload):
    if isinstance(payload,list):return payload
    if not isinstance(payload,dict):return []
    for key in ("products","items"):
        if isinstance(payload.get(key),list):return payload[key]
        if isinstance(payload.get(key),dict):return list(payload[key].values())
    return [x for x in payload.values() if isinstance(x,dict)]

def sku_values(product):
    raw=product.get("skus")
    if isinstance(raw,dict):raw=list(raw.values())
    return [trim(x.get("sku")) for x in raw if isinstance(x,dict)] if isinstance(raw,list) else []

def main():
    root=ET.parse(FEED).getroot()
    offers=[]
    for node in root.findall("./shop/offers/offer"):
        oid=trim(node.get("id"))
        name=trim(node.findtext("name"))
        model=trim(node.findtext("model"))
        if not oid or not model or not name:continue
        offers.append({"offer_id":oid,"name":name,"normalized_name":norm(name),"model":model})
    if len(offers)<750:raise RuntimeError("Too few offers, refusing to repair")
    if len({o["offer_id"] for o in offers})!=len(offers):raise RuntimeError("Duplicate YML ID")
    client=WebasystClient(min_request_interval=0.15)
    products=[]
    offset=0
    while True:
        block=products_list(client.call("shop.product.search",params={
            "hash":f"type/{TYPE}","offset":offset,"limit":1000,"fields":"*,skus,stock_counts"}))
        products.extend(block)
        if len(block)<1000:break
        offset+=len(block)
        if offset>3000:raise RuntimeError("Unexpectedly many products")
    if len(products)<800:raise RuntimeError("Webasyst Aletan type unexpectedly small")
    by_name=collections.defaultdict(list)
    by_yml=collections.defaultdict(list)
    by_sku=collections.defaultdict(list)
    for p in products:
        if int(p.get("type_id") or 0)!=TYPE:continue
        if int(p.get("yml_profile_id") or 0)!=PROFILE:continue
        name=norm(p.get("name"))
        if name:by_name[name].append(p)
        yml=trim(p.get("yml_id"))
        if yml:by_yml[yml].append(p)
        for sku in sku_values(p):
            if sku:by_sku[sku].append(p)
    code_offers=collections.Counter(o["model"] for o in offers)
    name_offers=collections.Counter(o["normalized_name"] for o in offers)
    names=set()
    candidates=[]
    exclusions=collections.Counter()
    for o in offers:
        oid=o["offer_id"]
        original=o["model"]
        key=o["normalized_name"]
        # Exact name is required even when a legacy external ID matches.
        by_external=by_yml.get(oid,[])
        if not by_external and oid!=original:
            by_external=by_yml.get(original,[])
        by_article=by_sku.get(original,[])
        # For duplicated supplier articles, the name must identify a distinct item.
        possible={str(p.get("id")):p for p in (by_external+by_article+by_name.get(key,[]))}
        verified=[]
        for p in possible.values():
            if norm(p.get("name")) != key:
                continue
            ext=trim(p.get("yml_id"))
            sku=sku_values(p)
            if ext not in ("",oid,original) and original not in sku:
                continue
            if ext=="" and original not in sku:
                continue
            if len(sku)!=1:
                continue
            verified.append(p)
        if len(verified)!=1:
            exclusions["no_unique_candidate" if len(verified)==0 else "ambiguous_candidate"]+=1
            continue
        p=verified[0]
        if code_offers[original]>1 or name_offers[key]>1 or len(by_name.get(key,[]))>1:
            # Requires a globally unique exact product name for variants.
            if name_offers[key]>1 or len(by_name.get(key,[]))>1:
                exclusions["duplicate_name"]+=1
                continue
            # Different variants can share vendorCode, so the exact name is the tie-breaker.
        if trim(p.get("yml_id"))=="" and original not in sku_values(p):
            exclusions["no_identifier_credential"]+=1
            continue
        candidates.append({
            "profile_id":PROFILE,"product_id":int(p["id"]),
            "offer_id":oid,"name":trim(p.get("name")),
            "current_yml_id":trim(p.get("yml_id")),
            "current_sku":sku_values(p)[0],
            "original_vendor_code":original
        })
    offer_count=collections.Counter(x["offer_id"] for x in candidates)
    product_count=collections.Counter(x["product_id"] for x in candidates)
    final=[x for x in candidates if offer_count[x["offer_id"]]==1 and product_count[x["product_id"]]==1]
    blocked=len(candidates)-len(final)
    if blocked:exclusions["non_bijective"]+=blocked
    # Ensure all planned IDs and mapped products are unique.
    assert len(final)==len({x["offer_id"] for x in final})==len({x["product_id"] for x in final})
    OUTPUT.write_text(json.dumps(final,ensure_ascii=False,indent=2),encoding="utf-8")
    print("ALETAN_PLAN",json.dumps({
        "feed_offers":len(offers),"webasyst_type_products":len(products),
        "eligible_unique_bindings":len(final),"excluded_offers":len(offers)-len(final),
        "excluded_reason":dict(exclusions),
        "unchanged_external_id":sum(x["current_yml_id"]==x["offer_id"] for x in final),
        "blank_external_id":sum(not x["current_yml_id"] for x in final),
        "variant_external_id_change":sum(x["current_yml_id"] not in ("",x["offer_id"]) for x in final),
        "contains_B102":any(x["offer_id"]=="B102" and x["product_id"]==1443656 for x in final),
        "contains_B1001":any(x["offer_id"]=="B1001" and x["product_id"]==1474987 for x in final),
        "file":str(OUTPUT)
    },ensure_ascii=False))
    if len(final)<600:raise RuntimeError("Too few unambiguous matches; no write allowed")

if __name__=="__main__":main()
