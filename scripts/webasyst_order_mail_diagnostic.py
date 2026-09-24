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


print("WA_STOREFRONT_MAIL_HISTORY_BEGIN=1")
hist = client.call("shop.order.search", params={"limit":120, "fields":"*,sales_channel"})
horders=(hist or {}).get("orders") or []
n=0
for o in horders:
    sc=o.get("sales_channel") or {}
    scid=str(sc.get("id") or "") if isinstance(sc,dict) else str(sc)
    if not scid.startswith("storefront:profikompany.ru"):
        continue
    oid=o.get("id")
    try:
        logs=client.call("shop.order.log", params={"id":oid}) or []
    except Exception:
        continue
    mail_like=0
    blank=0
    for x in logs:
        if not isinstance(x,dict):
            continue
        aid=str(x.get("action_id") or "")
        if aid=="":
            blank+=1
        txt=str(x.get("text") or "").lower()
        rec=str(x.get("log_record") or "").lower()
        if aid=="" and ("notification" in txt or "уведомлен" in txt or "email" in txt or "envelope" in txt or "письм" in txt or "notification" in rec):
            mail_like+=1
    n+=1
    out(f"WA_SF_{n}_ID",oid)
    out(f"WA_SF_{n}_CREATED",o.get("create_datetime"))
    out(f"WA_SF_{n}_STATE",o.get("state_id"))
    out(f"WA_SF_{n}_BLANK_LOGS",blank)
    out(f"WA_SF_{n}_MAIL_LOGS",mail_like)
    if n>=20:
        break
out("WA_STOREFRONT_HISTORY_COUNT",n)


print("WA_SELECTED_LOG_TIMELINE_BEGIN=1")
for oid in [41037,41028,40618,40482]:
    try:
        logs=client.call("shop.order.log", params={"id":oid}) or []
    except Exception as e:
        out(f"WA_TL_{oid}_ERROR",type(e).__name__)
        continue
    for j,x in enumerate(logs,1):
        if not isinstance(x,dict): continue
        out(f"WA_TL_{oid}_{j}_ACTION",str(x.get("action_id") or ""))
        out(f"WA_TL_{oid}_{j}_DATETIME",x.get("datetime") or x.get("create_datetime") or "")
        out(f"WA_TL_{oid}_{j}_KEYS",",".join(sorted(x.keys())))
        # classify only; never print text/params values
        blob=(" ".join(str(x.get(k) or "") for k in ["text","log_record","action_name"])).lower()
        out(f"WA_TL_{oid}_{j}_SEND_WORD",int(any(s in blob for s in ["отправ","уведом","email","e-mail","mail","письм"])))

# diagnostic trigger 2026-09-22

# contact email presence diagnostic
for oid in [41037, 41028, 40923]:
    try:
        info = client.call("shop.order.getInfo", params={"id": oid}) or {}
        contact = info.get("contact") or {}
        email = (contact.get("email") or "").strip()
        out(f"WA_ORDER_{oid}_CONTACT_EMAIL_PRESENT", int(bool(email)))
        out(f"WA_ORDER_{oid}_CONTACT_ID_PRESENT", int(bool(info.get("contact_id"))))
        params = info.get("params") or {}
        storefront = str(params.get("storefront") or "")
        out(f"WA_ORDER_{oid}_STOREFRONT_MATCH", int(storefront == "profikompany.ru"))
    except Exception as e:
        out(f"WA_ORDER_{oid}_INFO_ERROR", type(e).__name__)


print("WA_CRM_EMAIL_SOURCE_DIAGNOSTIC_BEGIN=1")
try:
    conv = client.call("crm.conversation.list", params={"transport":"EMAIL","limit":20}) or {}
    rows = conv.get("data") if isinstance(conv,dict) else []
    if not isinstance(rows,list):
        rows=[]
    providers=[]
    source_names=[]
    for row in rows:
        if not isinstance(row,dict):
            continue
        src=row.get("source") if isinstance(row.get("source"),dict) else {}
        provider=str(src.get("provider") or "").strip()
        name=str(src.get("name") or "").strip()
        if provider:
            providers.append(provider)
        if name:
            source_names.append(name)
    out("WA_CRM_EMAIL_CONVERSATIONS",len(rows))
    out("WA_CRM_EMAIL_SOURCE_PROVIDERS",",".join(sorted(set(providers))))
    out("WA_CRM_EMAIL_SOURCE_NAMES",",".join(sorted(set(source_names))))
except Exception as e:
    out("WA_CRM_EMAIL_SOURCE_ERROR",type(e).__name__)
