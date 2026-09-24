#!/usr/bin/env python3
import json
import os
import time
from datetime import datetime, timedelta, timezone

import requests

TOKEN=(os.getenv("WB_API_TOKEN") or "").strip()
if not TOKEN:
    raise SystemExit("WB_API_TOKEN is missing")

H={"Authorization":TOKEN,"Accept":"application/json"}

def get(url, **kwargs):
    r=requests.get(url,headers=H,timeout=60,**kwargs)
    return r

# Recent DBS/EDBS orders from the public Marketplace API.
orders=[]
r=get("https://marketplace-api.wildberries.ru/api/v3/dbs/orders/new")
if r.ok:
    orders += [x for x in (r.json().get("orders") or []) if isinstance(x,dict)]

now=datetime.now(timezone.utc)
start=now-timedelta(days=7)
params={
    "limit":1000,
    "next":0,
    "dateFrom":int(start.timestamp()),
    "dateTo":int(now.timestamp()),
}
r2=get("https://marketplace-api.wildberries.ru/api/v3/dbs/orders",params=params)
if r2.ok:
    orders += [x for x in (r2.json().get("orders") or []) if isinstance(x,dict)]

by_rid={}
for o in orders:
    rid=str(o.get("rid") or "").strip()
    if rid:
        by_rid[rid]={
            "order_id":o.get("id"),
            "rid":rid,
            "delivery_type":o.get("deliveryType"),
        }

# Also compare chat rid values with the recent Statistics orders used by the Webasyst sync.
stats_rids=set()
stats_r=get(
    "https://statistics-api.wildberries.ru/api/v1/supplier/orders",
    params={"dateFrom":(now-timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S"),"flag":0},
)
if stats_r.ok and isinstance(stats_r.json(),list):
    for row in stats_r.json():
        if isinstance(row,dict):
            rid=str(row.get("srid") or "").strip()
            if rid:
                stats_rids.add(rid)

# Public Buyers Chat API.
chat_r=get("https://buyer-chat-api.wildberries.ru/api/v1/seller/chats")
result={
    "orders_http":[r.status_code,r2.status_code],
    "recent_dbs_orders":len(by_rid),
    "chat_http":chat_r.status_code,
    "chat_access":chat_r.ok,
    "chats_total":0,
    "matched_order_chats":[],
    "statistics_http":stats_r.status_code,
    "statistics_rids":len(stats_rids),
    "matched_statistics_chats":0,
}
if chat_r.ok:
    data=chat_r.json() if chat_r.content else {}
    chats=data.get("result") or []
    if not isinstance(chats,list):
        chats=[]
    result["chats_total"]=len(chats)
    for ch in chats:
        if not isinstance(ch,dict):
            continue
        good=ch.get("goodCard") if isinstance(ch.get("goodCard"),dict) else {}
        rid=str(good.get("rid") or "").strip()
        if rid and rid in by_rid:
            result["matched_order_chats"].append({
                "order_id":by_rid[rid].get("order_id"),
                "delivery_type":by_rid[rid].get("delivery_type"),
                "has_replySign":bool(ch.get("replySign")),
                "has_chatID":bool(ch.get("chatID")),
            })
        if rid and rid in stats_rids:
            result["matched_statistics_chats"] += 1
else:
    result["chat_error"]=chat_r.text[:500]

print(json.dumps(result,ensure_ascii=False,indent=2))


# Recent chat events diagnostic (privacy-safe: no message text or customer names).
recent_start_ms=int((now-timedelta(hours=1)).timestamp()*1000)
events_r=get(
    "https://buyer-chat-api.wildberries.ru/api/v1/seller/events",
    params={"next": recent_start_ms},
)
result["events_http"]=events_r.status_code
result["recent_events_total"]=0
result["recent_message_events"]=0
result["recent_seller_messages"]=0
result["recent_client_messages"]=0
result["latest_seller_message_time"]=None
result["latest_client_message_time"]=None

if events_r.ok:
    data=events_r.json() if events_r.content else {}
    root=data.get("result") if isinstance(data,dict) and isinstance(data.get("result"),dict) else data
    events=root.get("events") if isinstance(root,dict) else []
    if not isinstance(events,list):
        events=[]
    result["recent_events_total"]=len(events)
    seller_times=[]
    client_times=[]
    for ev in events:
        if not isinstance(ev,dict):
            continue
        kind=str(ev.get("eventType") or ev.get("type") or "").strip().lower()
        if kind not in ("","message"):
            continue
        result["recent_message_events"] += 1
        sender=str(ev.get("sender") or "").strip().lower()
        when=str(ev.get("addTime") or "").strip() or None
        if sender=="seller":
            result["recent_seller_messages"] += 1
            if when:
                seller_times.append(when)
        elif sender=="client":
            result["recent_client_messages"] += 1
            if when:
                client_times.append(when)
    if seller_times:
        result["latest_seller_message_time"]=max(seller_times)
    if client_times:
        result["latest_client_message_time"]=max(client_times)
else:
    result["events_error"]=events_r.text[:500]

print("\nCHAT_EVENT_DIAGNOSTIC")
print(json.dumps({
    "events_http":result.get("events_http"),
    "recent_events_total":result.get("recent_events_total"),
    "recent_message_events":result.get("recent_message_events"),
    "recent_seller_messages":result.get("recent_seller_messages"),
    "recent_client_messages":result.get("recent_client_messages"),
    "latest_seller_message_time":result.get("latest_seller_message_time"),
    "latest_client_message_time":result.get("latest_client_message_time"),
},ensure_ascii=False,indent=2))


# Check recent lastMessage timestamps from seller/chats without calling seller/events.
recent_last_messages=0
latest_last_message_ms=0
latest_last_message_iso=None
if chat_r.ok:
    data=chat_r.json() if chat_r.content else {}
    chats_for_last=data.get("result") or []
    if isinstance(chats_for_last,list):
        cutoff_ms=int((now-timedelta(hours=1)).timestamp()*1000)
        for ch in chats_for_last:
            if not isinstance(ch,dict):
                continue
            lm=ch.get("lastMessage") if isinstance(ch.get("lastMessage"),dict) else {}
            try:
                ts=int(lm.get("addTimestamp") or 0)
            except (TypeError,ValueError):
                ts=0
            if ts>=cutoff_ms:
                recent_last_messages += 1
            if ts>latest_last_message_ms:
                latest_last_message_ms=ts
        if latest_last_message_ms>0:
            latest_last_message_iso=datetime.fromtimestamp(latest_last_message_ms/1000,timezone.utc).isoformat()

print("\nCHAT_LAST_MESSAGE_DIAGNOSTIC")
print(json.dumps({
    "recent_last_messages_1h":recent_last_messages,
    "latest_last_message_ms":latest_last_message_ms,
    "latest_last_message_iso":latest_last_message_iso,
},ensure_ascii=False,indent=2))

# diagnostic refresh trigger 2026-09-24
