#!/usr/bin/env python3
import json, os, sys
from collections import defaultdict, Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webasyst"))
from client import WebasystClient

wa=WebasystClient(min_request_interval=0.15)
settings=wa.call("shop.settings.get")
states=settings.get("order_states") or []
if isinstance(states,dict): states=list(states.values())
out={"states":[],"observed_transitions":[],"stock_counting_action":settings.get("stock_counting_action"),"ignore_stock_count":settings.get("ignore_stock_count")}
for st in states:
    if not isinstance(st,dict): continue
    out["states"].append({
      "id":st.get("id"),"name":st.get("name"),
      "available_actions":st.get("available_actions") or []
    })

# scan recent orders and learn actual action->state transitions
payload=wa.call("shop.order.search",params={"limit":200,"sort":"updated DESC"})
orders=payload.get("orders") if isinstance(payload,dict) else []
seen=Counter()
for o in orders or []:
    oid=o.get("id")
    if not oid: continue
    try:
        logs=wa.call("shop.order.log",params={"id":oid})
    except Exception:
        continue
    rows=logs if isinstance(logs,list) else (logs.get("log") or logs.get("items") or []) if isinstance(logs,dict) else []
    for row in rows:
        if not isinstance(row,dict): continue
        a=str(row.get("action_id") or "")
        b=str(row.get("before_state_id") or "")
        c=str(row.get("after_state_id") or "")
        n=str(row.get("action_name") or "")
        if a and b and c and b!=c:
            seen[(a,n,b,c)] += 1
out["observed_transitions"]=[
  {"action_id":a,"action_name":n,"from":b,"to":c,"count":cnt}
  for (a,n,b,c),cnt in seen.most_common()
]
Path("webasyst/order_transition_audit.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(out,ensure_ascii=False,indent=2))
