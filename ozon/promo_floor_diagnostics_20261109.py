#!/usr/bin/env python3
"""Read-only diagnosis of stubborn below-floor Ozon promo participants."""
import json
from collections import Counter
from pathlib import Path
from promo_v2_reconcile import api, pages, load_prices, num

prices=load_prices()
actions=api("/v1/actions",method="GET").get("result") or []
bad=[]; by_mode=Counter(); by_autoflag=Counter(); by_action=Counter(); examples=[]
for action in actions:
    aid=int(action["id"])
    rows=pages("/v2/actions/products",aid)
    for row in rows:
        pid=int(row.get("id") or 0)
        p=prices.get(pid)
        if not p:continue
        info=p.get("price") or {}
        floor=max(num(info.get("min_price")),num(row.get("min_seller_price")))
        act=num(row.get("action_price"))
        if act<=0 or floor<=0 or act>=floor:continue
        record={
            "action_id":aid,"action_title":action.get("title"),"product_id":pid,
            "offer_id":p.get("offer_id"),"action_price":str(act),
            "min_price":str(num(info.get("min_price"))),"min_seller_price":str(num(row.get("min_seller_price"))),
            "max_action_price":str(num(row.get("max_action_price"))),
            "add_mode":row.get("add_mode"),"stock":row.get("stock"),
            "auto_action_enabled":info.get("auto_action_enabled"),
            "auto_add_enabled":info.get("auto_add_to_ozon_actions_list_enabled"),
            "price_fields":sorted(list(info.keys())),
        }
        bad.append(record)
        by_mode[str(record["add_mode"])]+=1
        by_autoflag[str(record["auto_action_enabled"])+"/"+str(record["auto_add_enabled"])]+=1
        by_action[str(aid)]+=1
        if len(examples)<12:examples.append(record)
print("DIAG_ACTIONS",[(a["id"],a.get("title")) for a in actions])
print("DIAG_TOTAL",len(bad),"BY_ACTION",dict(by_action),"BY_MODE",dict(by_mode),"AUTO_FLAGS",dict(by_autoflag),flush=True)
print("DIAG_SAMPLES",json.dumps(examples,ensure_ascii=False),flush=True)
if bad:
    sample_ids=[str(x["product_id"]) for x in bad[:3]]
    for payload in ({"product_ids":sample_ids},{"product_id":sample_ids}):
        try:
            d=api("/v1/product/action/timer/status",payload)
            print("TIMER_STATUS",json.dumps(d,ensure_ascii=False)[:2300],flush=True)
        except Exception as e:
            print("TIMER_ERROR",str(e)[:1300],flush=True)
Path("ozon/promo_floor_violations_20261109.json").write_text(json.dumps(bad,ensure_ascii=False,indent=2),encoding="utf-8")
