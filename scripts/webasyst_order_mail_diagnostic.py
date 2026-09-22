#!/usr/bin/env python3
import os, json
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webasyst"))
from client import WebasystClient, WebasystAPIError

EXPECTED_ADMIN = "shop@office-mag.com"
client = WebasystClient()

def out(k,v):
    print(f"{k}={'' if v is None else v}")

settings = client.call("shop.settings.get")
shop_settings = (settings or {}).get("settings") or {}
primary = (shop_settings.get("email") or "").strip().lower()
out("WA_VERSION", (settings or {}).get("version"))
out("WA_PRIMARY_EMAIL_MATCH_EXPECTED", int(primary == EXPECTED_ADMIN))
out("WA_PRIMARY_EMAIL_DOMAIN", primary.split("@",1)[1] if "@" in primary else "")
out("WA_SERVER_TIME", (settings or {}).get("server_time"))
out("WA_SERVER_TZ", (settings or {}).get("server_timezone"))

orders_resp = client.call("shop.order.search", params={"limit":20, "fields":"*,sales_channel"})
orders = (orders_resp or {}).get("orders") or []
out("WA_ORDERS_RETURNED", len(orders))
out("WA_ORDERS_TOTAL", (orders_resp or {}).get("count"))

now = datetime.now(timezone.utc)
recent_24=0
recent_7d=0
for o in orders:
    ds=o.get("create_datetime")
    if not ds: continue
    try:
        dt=datetime.strptime(ds,"%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        continue
    if now-dt <= timedelta(days=1): recent_24 += 1
    if now-dt <= timedelta(days=7): recent_7d += 1
out("WA_ORDERS_LAST_24H",recent_24)
out("WA_ORDERS_LAST_7D",recent_7d)

for i,o in enumerate(orders[:8],1):
    oid=o.get("id")
    out(f"WA_ORDER_{i}_ID", oid)
    out(f"WA_ORDER_{i}_CREATED", o.get("create_datetime"))
    out(f"WA_ORDER_{i}_STATE", o.get("state_id"))
    out(f"WA_ORDER_{i}_SALES_CHANNEL", o.get("sales_channel"))
    try:
        logs=client.call("shop.order.log", params={"id":oid})
    except Exception as e:
        out(f"WA_ORDER_{i}_LOG_ERROR", type(e).__name__)
        continue
    logs=logs or []
    out(f"WA_ORDER_{i}_LOG_COUNT",len(logs))
    actions=[str(x.get("action_id") or "") for x in logs if isinstance(x,dict)]
    out(f"WA_ORDER_{i}_ACTIONS", ",".join(actions[:12]))

# Probe documented methods only; no writes.
for label,method,params in [
    ("SETTINGS","shop.settings.get",{}),
    ("ORDERS","shop.order.search",{"limit":1,"fields":"id,create_datetime,state_id"}),
]:
    try:
        client.call(method,params=params)
        out(f"WA_{label}_API_OK",1)
    except Exception as e:
        out(f"WA_{label}_API_OK",0)
        out(f"WA_{label}_API_ERROR",type(e).__name__)
