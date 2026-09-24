#!/usr/bin/env python3
import json
import os
import requests
from datetime import datetime, timezone, timedelta

TOKEN=(os.getenv("WB_API_TOKEN") or "").strip()
if not TOKEN:
    raise SystemExit("WB_API_TOKEN missing")

url="https://buyer-chat-api.wildberries.ru/api/v1/seller/chats"
r=requests.get(url,headers={"Authorization":TOKEN,"Accept":"application/json"},timeout=90)

out={
    "http":r.status_code,
    "retry_after":r.headers.get("X-RateLimit-Retry") or r.headers.get("Retry-After"),
    "rate_limit_reset":r.headers.get("X-RateLimit-Reset"),
    "rate_limit_remaining":r.headers.get("X-RateLimit-Remaining"),
    "chats_total":0,
    "recent_last_messages_1h":0,
    "latest_last_message_iso":None,
    "chat_keys":[],
    "last_message_keys":[],
}

if r.ok:
    data=r.json() if r.content else {}
    chats=data.get("result") if isinstance(data,dict) else []
    chats=chats if isinstance(chats,list) else []
    out["chats_total"]=len(chats)
    cutoff=int((datetime.now(timezone.utc)-timedelta(hours=1)).timestamp()*1000)
    latest=0
    first=chats[0] if chats and isinstance(chats[0],dict) else {}
    out["chat_keys"]=sorted(first.keys())
    lm0=first.get("lastMessage") if isinstance(first.get("lastMessage"),dict) else {}
    out["last_message_keys"]=sorted(lm0.keys())
    for ch in chats:
        if not isinstance(ch,dict):
            continue
        lm=ch.get("lastMessage") if isinstance(ch.get("lastMessage"),dict) else {}
        try:
            ts=int(lm.get("addTimestamp") or 0)
        except (TypeError,ValueError):
            ts=0
        latest=max(latest,ts)
        if ts>=cutoff:
            out["recent_last_messages_1h"]+=1
    if latest:
        out["latest_last_message_iso"]=datetime.fromtimestamp(latest/1000,timezone.utc).isoformat()
else:
    out["error"]=r.text[:500]

print(json.dumps(out,ensure_ascii=False,indent=2))
