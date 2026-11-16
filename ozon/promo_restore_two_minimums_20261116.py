#!/usr/bin/env python3
"""Restore the exact pre-detach Ozon minimum prices for two November 16 SKUs.

Fix only the two price floors that the platform lowered while auto-price strategy was detached.
Do not change marketing, inventory, other products, nor existing discounts.
"""
import json,time
from decimal import Decimal
from promo_v2_reconcile import api,load_prices,num
RESTORE={1222016955:Decimal("35736"),4482063332:Decimal("26031")}
def snapshot():
    return load_prices()
before=snapshot()
for pid,orig in RESTORE.items():
    p=before.get(pid)
    if not p:raise RuntimeError(f"Missing targeted Ozon product {pid}")
    info=p.get("price") or {}
    cur_min=num(info.get("min_price"))
    cur=num(info.get("price"))
    old=num(info.get("old_price"))
    if cur_min>orig:
        raise RuntimeError(f"Refuse to lower an increased minimum {pid}: {cur_min} > {orig}")
    if cur_min==orig and cur>=orig:
        print("ALREADY_RESTORED",pid,"price",cur,"minimum",cur_min,flush=True)
        continue
    new_price=max(cur,orig+Decimal("1"))
    if old>0 and old<=new_price:raise RuntimeError(f"Old price is below new safe price for {pid}; no change")
    payload={"prices":[{
        "offer_id":p["offer_id"],"currency_code":"RUB",
        "price":str(new_price),"old_price":str(old),
        "min_price":str(orig),"auto_action_enabled":"UNKNOWN"
    }]}
    r=api("/v1/product/import/prices",payload)
    print("RESTORE_SUBMIT",pid,p["offer_id"],json.dumps(r,ensure_ascii=False),flush=True)
    rr=(r.get("result") or [{}])[0]
    if not rr.get("updated") or rr.get("errors"):
        raise RuntimeError("Restoration rejected: "+str(rr))
time.sleep(10)
after=snapshot()
for pid,orig in RESTORE.items():
    p=after[pid]
    info=p.get("price") or {}
    print("RESTORE_READBACK",pid,"offer",p.get("offer_id"),"current",info.get("price"),"minimum",info.get("min_price"),"old",info.get("old_price"),flush=True)
    if num(info.get("min_price"))!=orig or num(info.get("price"))<orig:
        raise RuntimeError("Failed to restore safe minimum on product "+str(pid))
print("BOTH_MINIMUMS_RESTORED",flush=True)
