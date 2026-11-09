#!/usr/bin/env python3
"""Safely probe direct Ozon v2 promotion deactivation on one under-floor SKU."""
import json,time
from promo_v2_reconcile import api,load_prices,pages,num
ACTION=1977747
PID=1109384964
OFFER="RED-00-00003510"
p=load_prices().get(PID)
if not p or p.get("offer_id")!=OFFER:raise SystemExit("Wrong SKU")
rows=pages("/v2/actions/products",ACTION)
row=next((r for r in rows if int(r.get("id") or 0)==PID),None)
if not row:raise SystemExit("Item already absent; nothing changed")
floor=max(num((p.get("price") or {}).get("min_price")),num(row.get("min_seller_price")))
actual=num(row.get("action_price"))
if not floor or actual>=floor:raise SystemExit("No below-floor violation")
print("BEFORE",json.dumps({"offer_id":OFFER,"floor":str(floor),"promo_price":str(actual)},ensure_ascii=False),flush=True)
try:
    d=api("/v2/actions/products/deactivate",{"action_id":ACTION,"product_ids":[PID]})
except Exception as e:
    print("DEACTIVATE_UNSUPPORTED_OR_ERROR",str(e)[:1600],flush=True)
    raise SystemExit(0)
print("V2_DEACTIVATE_RESPONSE",json.dumps(d,ensure_ascii=False)[:3000],flush=True)
time.sleep(8)
after=pages("/v2/actions/products",ACTION)
new=next((r for r in after if int(r.get("id") or 0)==PID),None)
print("AFTER",json.dumps({"still_present":bool(new),"action_price":str(num((new or {}).get("action_price"))),"minimum_before":str(floor)},ensure_ascii=False),flush=True)
