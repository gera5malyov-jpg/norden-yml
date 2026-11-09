#!/usr/bin/env python3
"""Safely detach one SKU from failing MIN_EXT_PRICE strategy and restore floor."""
import json,time
from promo_v2_reconcile import api,load_prices,num

PID=1100495563
OFFER="RED-00-00011553"
STRATEGY="3abf9219-eb93-4cd1-b483-05c2290e3614"
ORIGINAL_FLOOR=26616
ORIGINAL_OLD_PRICE=42295
TARGET_PRICE=26617

def product():
    p=load_prices().get(PID)
    if not p or p.get("offer_id")!=OFFER:raise RuntimeError("Unexpected product identity")
    return p.get("price") or {}

def strategy_ids():
    d=api("/v1/pricing-strategy/strategy-ids-by-product-ids",{"product_id":[str(PID)]})
    return [str(x.get("strategy_id")) for x in (d.get("result") or {}).get("products_info") or [] if int(x.get("product_id") or 0)==PID]

before=product()
print("BEFORE",json.dumps({"price":before.get("price"),"min_price":before.get("min_price"),"old_price":before.get("old_price"),"strategies":strategy_ids()},ensure_ascii=False),flush=True)
if int(before.get("old_price") or 0)!=ORIGINAL_OLD_PRICE:
    raise SystemExit("Old price differs; refusing to overwrite")
if STRATEGY not in strategy_ids():
    raise SystemExit("SKU no longer in expected strategy; no changes")
deleted=api("/v1/pricing-strategy/products/delete",{"product_id":[str(PID)]})
print("STRATEGY_DELETE",json.dumps(deleted,ensure_ascii=False)[:2300],flush=True)
if int((deleted.get("result") or {}).get("failed_product_count") or 0):
    raise SystemExit("Strategy removal rejected")
time.sleep(5)
still=strategy_ids()
print("STRATEGY_AFTER",still,flush=True)
if still:raise SystemExit("SKU still linked to strategy")
now=product()
if num(now.get("price"))<ORIGINAL_FLOOR or num(now.get("min_price"))!=ORIGINAL_FLOOR:
    payload={"prices":[{
       "offer_id":OFFER,"currency_code":"RUB","price":str(TARGET_PRICE),
       "old_price":str(ORIGINAL_OLD_PRICE),"min_price":str(ORIGINAL_FLOOR),
       "auto_action_enabled":"UNKNOWN"
    }]}
    result=api("/v1/product/import/prices",payload)
    print("RESTORE_PRICE",json.dumps(result,ensure_ascii=False)[:2000],flush=True)
    if not (result.get("result") or [{}])[0].get("updated"):
        raise SystemExit("Price restoration rejected")
    time.sleep(7)
last=product()
print("FINAL",json.dumps({"price":last.get("price"),"min_price":last.get("min_price"),"old_price":last.get("old_price"),"strategy_ids":strategy_ids()},ensure_ascii=False),flush=True)
if num(last.get("min_price"))!=ORIGINAL_FLOOR or num(last.get("price"))<ORIGINAL_FLOOR:
    raise SystemExit("Original floor and price not restored")
