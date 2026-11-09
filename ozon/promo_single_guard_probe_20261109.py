#!/usr/bin/env python3
"""Guarded one-SKU probe of persistent Ozon below-floor promotion enrollment."""
import json,time
from promo_v2_reconcile import api,load_prices,pages,num,money
PID=1100495563
OFFER="RED-00-00011553"
ACTION=1977747

def get_state():
    bypid=load_prices()
    product=bypid.get(PID)
    if not product or str(product.get("offer_id"))!=OFFER:
        raise RuntimeError("Unexpected Ozon product identity; abort")
    rows=pages("/v2/actions/products",ACTION)
    part=next((r for r in rows if int(r.get("id") or 0)==PID),None)
    return product,part

p,row=get_state()
if not row:
    raise SystemExit("Already absent; no modifications")
floor=max(num(p.get("price",{}).get("min_price")),num(row.get("min_seller_price")))
current=num(row.get("action_price"))
maximum=num(row.get("max_action_price"))
if floor <=0 or current >=floor or maximum <=0:
    raise SystemExit("No active known below-floor violation; not modifying")
print("BEFORE",json.dumps({"offer_id":OFFER,"min_price":str(floor),"action_price":str(current),"max_action_price":str(maximum),"flags":p.get("price")},ensure_ascii=False)[:3500],flush=True)

flags=api("/v1/product/import/prices",{"prices":[{
    "offer_id":OFFER,
    "auto_action_enabled":"DISABLED",
    "auto_add_to_ozon_actions_list_enabled":"DISABLED",
    "min_price_for_auto_actions_enabled":True
}]})
print("FLAGS_RESPONSE",json.dumps(flags,ensure_ascii=False)[:2300],flush=True)
r=flags.get("result") or []
if not r or not r[0].get("updated") is True:
    raise SystemExit("Could not disable auto-enrollment, not removing")
p,_=get_state()
print("FLAGS_READBACK",json.dumps({k:p.get("price",{}).get(k) for k in ("auto_action_enabled","auto_add_to_ozon_actions_list_enabled","min_price")}),flush=True)
if p.get("price",{}).get("auto_action_enabled") or p.get("price",{}).get("auto_add_to_ozon_actions_list_enabled"):
    raise SystemExit("Auto flags still active after setting; abort")
leave_price=max(floor,maximum+1)
u=api("/v1/actions/products/update",{"action_id":ACTION,"products":[{"product_id":PID,"action_price":money(leave_price)}]})
print("REMOVAL_RESPONSE",json.dumps(u,ensure_ascii=False)[:2300],flush=True)
if PID not in [int(i) for i in u.get("deactivated_product_ids") or []]:
    raise SystemExit("Ozon did not confirm deactivation")
time.sleep(12)
after,part=get_state()
print("AFTER",json.dumps({
    "present":bool(part),
    "action_price":str(num((part or {}).get("action_price"))),
    "min_price":str(num(after.get("price",{}).get("min_price"))),
    "flags":{"auto_action_enabled":after.get("price",{}).get("auto_action_enabled"),
              "auto_add_to_ozon_actions_list_enabled":after.get("price",{}).get("auto_add_to_ozon_actions_list_enabled")}
},ensure_ascii=False),flush=True)
