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
