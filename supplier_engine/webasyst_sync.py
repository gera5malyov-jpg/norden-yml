from __future__ import annotations
from dataclasses import asdict
from typing import Iterable
from webasyst.client import WebasystClient
from .models import Product

def _listify(payload, keys=("products","items")):
    if isinstance(payload,list): return [x for x in payload if isinstance(x,dict)]
    if isinstance(payload,dict):
        for k in keys:
            v=payload.get(k)
            if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
            if isinstance(v,dict): return [x for x in v.values() if isinstance(x,dict)]
    return []

def index_by_sku(wa: WebasystClient):
    out={}
    offset=0
    while True:
        p=wa.call("shop.product.search",params={"offset":offset,"limit":1000,"fields":"id,name,skus"})
        rows=_listify(p)
        for product in rows:
            skus=product.get("skus") or []
            if isinstance(skus,dict): skus=list(skus.values())
            for sku in skus:
                code=str(sku.get("sku") or "").strip()
                if code: out.setdefault(code,[]).append((product,sku))
        if len(rows)<1000: break
        offset+=len(rows)
    return out

def build_plan(products: Iterable[Product], existing):
    plan={"create":[],"update":[],"blocked":[]}
    for p in products:
        matches=existing.get(p.sku,[])
        if len(matches)>1:
            plan["blocked"].append({"sku":p.sku,"reason":"duplicate_webasyst_sku","matches":len(matches)})
        elif len(matches)==1:
            product,sku=matches[0]
            plan["update"].append({"sku":p.sku,"product_id":product.get("id"),"sku_id":sku.get("id"),"desired":asdict(p)})
        else:
            plan["create"].append({"sku":p.sku,"desired":asdict(p)})
    return plan

def apply_plan(wa: WebasystClient, plan, *, allow_create=False, update_prices=True, update_stock=True):
    if plan["blocked"]: raise RuntimeError("Write blocked: duplicate SKU exists in Webasyst")
    result={"created":0,"updated":0}
    for row in plan["update"]:
        d=row["desired"]; data={}
        if update_prices:
            if d.get("price") is not None: data["price"]=d["price"]
            if d.get("compare_price") is not None: data["compare_price"]=d["compare_price"]
        if update_stock and d.get("stock") is not None: data["count"]=d["stock"]
        if data:
            wa.call("shop.product.skus.update",http_method="POST",params={"id":row["sku_id"]},data=data)
            result["updated"]+=1
    if plan["create"] and not allow_create:
        raise RuntimeError("Write blocked: config does not allow creating new products")
    # Generic creation intentionally stays disabled until supplier type/category/stock mapping is explicit.
    if plan["create"]:
        raise RuntimeError("Generic create requires explicit Webasyst type/category/stock mapping")
    return result
