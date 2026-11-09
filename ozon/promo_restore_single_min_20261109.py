#!/usr/bin/env python3
"""Repair original seller floor accidentally lowered on one guarded Ozon probe.

The original floor before the probe was 26616 RUB. Change no other product.
"""
import json,time
from promo_v2_reconcile import api,load_prices,num
PID=1100495563
OFFER="RED-00-00011553"
EXPECTED_NOW=26614
RESTORE_MIN=26616
TARGET_PRICE=26617
ORIGINAL_OLD_PRICE=42295

p=load_prices().get(PID)
if not p or p.get("offer_id")!=OFFER:
    raise SystemExit("Unexpected SKU identity")
info=p.get("price") or {}
current_price=num(info.get("price"))
current_min=num(info.get("min_price"))
print("BEFORE_RESTORE",json.dumps({"price":str(current_price),"min_price":str(current_min),"old_price":info.get("old_price"),"auto_action_enabled":info.get("auto_action_enabled"),"auto_add_enabled":info.get("auto_add_to_ozon_actions_list_enabled")},ensure_ascii=False),flush=True)
if current_min==RESTORE_MIN and current_price>=RESTORE_MIN:
    print("ALREADY_RESTORED",flush=True)
    raise SystemExit(0)
if current_price!=EXPECTED_NOW or current_min!=EXPECTED_NOW or int(info.get("old_price") or 0)!=ORIGINAL_OLD_PRICE:
    raise SystemExit("SKU changed unexpectedly; would not overwrite")
r=api("/v1/product/import/prices",{"prices":[{
   "offer_id":OFFER,
   "currency_code":"RUB",
   "price":str(TARGET_PRICE),
   "old_price":str(ORIGINAL_OLD_PRICE),
   "min_price":str(RESTORE_MIN),
   "auto_action_enabled":"UNKNOWN"
}]})
print("RESTORE_RESPONSE",json.dumps(r,ensure_ascii=False)[:3500],flush=True)
result=r.get("result") or []
if not result or result[0].get("updated") is not True:
    raise SystemExit("Ozon rejected restoration")
time.sleep(4)
after=(load_prices().get(PID) or {}).get("price") or {}
print("RESTORE_READBACK",json.dumps({"price":after.get("price"),"min_price":after.get("min_price"),"old_price":after.get("old_price"),"auto_action_enabled":after.get("auto_action_enabled"),"auto_add_enabled":after.get("auto_add_to_ozon_actions_list_enabled")},ensure_ascii=False),flush=True)
if num(after.get("min_price"))!=RESTORE_MIN or num(after.get("price"))<RESTORE_MIN:
    raise SystemExit("Restoration did not persist")
