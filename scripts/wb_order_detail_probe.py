#!/usr/bin/env python3
import os, time, requests
from datetime import datetime, timedelta, timezone

TARGET=int(os.environ.get("TARGET_WB_ORDER_ID","5436356732"))
TOKEN=os.environ["WB_API_TOKEN"].strip()
H={"Authorization":TOKEN,"Accept":"application/json","Content-Type":"application/json"}
BASE="https://marketplace-api.wildberries.ru"

def get(path, params=None):
    return requests.get(BASE+path,headers=H,params=params,timeout=60)

def post(path, body):
    return requests.post(BASE+path,headers=H,json=body,timeout=60)

found=None
model=None

# DBS new
r=get("/api/v3/dbs/orders/new")
print("DBS_NEW_HTTP="+str(r.status_code))
if r.ok:
    for o in (r.json().get("orders") or []):
        if int(o.get("id") or 0)==TARGET:
            found=o; model="dbs_new"; break

# DBS completed, last 60 days in two <=30-day windows
if not found:
    now=datetime.now(timezone.utc)
    for days_back in (0,30):
        end=now-timedelta(days=days_back)
        start=end-timedelta(days=29)
        nxt=0
        for _ in range(20):
            rr=get("/api/v3/dbs/orders",{
                "limit":1000,
                "next":nxt,
                "dateFrom":int(start.timestamp()),
                "dateTo":int(end.timestamp()),
            })
            print("DBS_DONE_HTTP="+str(rr.status_code))
            if not rr.ok: break
            d=rr.json()
            rows=d.get("orders") or []
            for o in rows:
                if int(o.get("id") or 0)==TARGET:
                    found=o; model="dbs_done"; break
            if found: break
            new_next=int(d.get("next") or 0)
            if not rows or not new_next or new_next==nxt: break
            nxt=new_next
        if found: break

# FBS new as fallback
if not found:
    rr=get("/api/v3/orders/new")
    print("FBS_NEW_HTTP="+str(rr.status_code))
    if rr.ok:
        for o in (rr.json().get("orders") or []):
            if int(o.get("id") or 0)==TARGET:
                found=o; model="fbs_new"; break

print("FOUND="+("1" if found else "0"))
print("MODEL="+str(model or ""))
if found:
    print("ORDER_FIELDS="+",".join(sorted(found.keys())))
    a=found.get("address")
    if isinstance(a,dict):
        print("ADDRESS_FIELDS="+",".join(sorted(a.keys())))
    # client endpoints; print only field names, never values
    for label,path in (("DBS_CLIENT","/api/v3/dbs/orders/client"),("FBS_CLIENT","/api/v3/orders/client")):
        cr=post(path,{"orders":[TARGET]})
        print(label+"_HTTP="+str(cr.status_code))
        if cr.ok:
            payload=cr.json()
            print(label+"_ROOT_FIELDS="+(",".join(sorted(payload.keys())) if isinstance(payload,dict) else type(payload).__name__))
            rows=payload.get("orders") or [] if isinstance(payload,dict) else []
            print(label+"_COUNT="+str(len(rows) if isinstance(rows,list) else -1))
            if rows and isinstance(rows[0],dict):
                print(label+"_FIELDS="+",".join(sorted(rows[0].keys())))
