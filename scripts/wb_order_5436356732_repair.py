#!/usr/bin/env python3
import os, sys, re, requests
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE/"webasyst"))
from client import WebasystClient

TARGET=int(os.environ.get("TARGET_WB_ORDER_ID","5436356732"))
TOKEN=os.environ["WB_API_TOKEN"].strip()
H={"Authorization":TOKEN,"Accept":"application/json","Content-Type":"application/json"}
BASE="https://marketplace-api.wildberries.ru"
wa=WebasystClient(min_request_interval=0.25)

def get(path,params=None):
    r=requests.get(BASE+path,headers=H,params=params,timeout=60)
    r.raise_for_status()
    return r.json()

def post(path,body):
    r=requests.post(BASE+path,headers=H,json=body,timeout=60)
    r.raise_for_status()
    return r.json()

# Locate completed DBS order within 60 days.
target=None
now=datetime.now(timezone.utc)
for back in (0,30):
    end=now-timedelta(days=back)
    start=end-timedelta(days=29)
    nxt=0
    for _ in range(20):
        d=get("/api/v3/dbs/orders",{
            "limit":1000,
            "next":nxt,
            "dateFrom":int(start.timestamp()),
            "dateTo":int(end.timestamp()),
        })
        rows=d.get("orders") or []
        for o in rows:
            if int(o.get("id") or 0)==TARGET:
                target=o
                break
        if target: break
        nn=int(d.get("next") or 0)
        if not rows or not nn or nn==nxt: break
        nxt=nn
    if target: break
if not target:
    raise SystemExit("TARGET_NOT_FOUND")

article=str(target.get("article") or "").strip()
created=str(target.get("createdAt") or "").strip()
address=(target.get("address") or {}).get("fullAddress") or ""
address=str(address).strip()

# Buyer info may be unavailable after completion. Use it only if WB still returns it.
buyer={}
try:
    bd=post("/api/v3/dbs/orders/client",{"orders":[TARGET]})
    rows=bd.get("orders") or []
    if rows and isinstance(rows[0],dict):
        buyer=rows[0]
except Exception:
    buyer={}

# Find the existing WB order in Webasyst. Historical importer used gNumber, so match
# by source + exact article/SKU + nearest source creation date rather than creating a duplicate.
sr=wa.call("shop.order.search",params={
    "hash":"search/params.mp_source=wildberries",
    "limit":100,
    "fields":"*,state"
})
rows=sr.get("orders") if isinstance(sr,dict) else sr if isinstance(sr,list) else []
if not rows:
    raise SystemExit("WEBASYST_WB_ORDER_NOT_FOUND")

try:
    target_dt=datetime.fromisoformat(created.replace("Z","+00:00")) if created else None
except Exception:
    target_dt=None

candidates=[]
for row in rows:
    oid=str(row.get("id") or "")
    if not oid: continue
    info=wa.call("shop.order.getInfo",params={"id":oid})
    params=info.get("params") or {}
    items=info.get("items") or []
    sku_codes={str(x.get("sku_code") or x.get("sku") or "").strip() for x in items if isinstance(x,dict)}
    if article and article not in sku_codes:
        continue
    score=0.0
    mp_created=str(params.get("mp_created_at") or "")
    if target_dt and mp_created:
        try:
            wd=datetime.fromisoformat(mp_created.replace("Z","+00:00"))
            score=abs((wd-target_dt).total_seconds())
        except Exception:
            score=999999999
    candidates.append((score,oid,info))

if not candidates:
    raise SystemExit("WEBASYST_MATCH_NOT_FOUND")
candidates.sort(key=lambda x:x[0])
if len(candidates)>1 and candidates[0][0]==candidates[1][0]:
    raise SystemExit("WEBASYST_MATCH_AMBIGUOUS")

_,oid,info=candidates[0]
params=dict(info.get("params") or {})
params["mp_wb_order_id"]=str(TARGET)
params["mp_wb_delivery_type"]=str(target.get("deliveryType") or "")
params["mp_wb_group_id"]=str(target.get("groupId") or "")
params["mp_wb_order_uid"]=str(target.get("orderUid") or "")
params["mp_wb_rid"]=str(target.get("rid") or "")
if address:
    params["shipping_address.street"]=address
    params["mp_wb_full_address"]=address
a=target.get("address") or {}
if a.get("latitude") is not None:
    params["mp_wb_latitude"]=str(a.get("latitude"))
if a.get("longitude") is not None:
    params["mp_wb_longitude"]=str(a.get("longitude"))

data={"id":oid,"params":params}
if address:
    data["shipping_address"]={"street":address}
if buyer:
    customer={}
    if str(buyer.get("fullName") or "").strip():
        customer["name"]=str(buyer.get("fullName")).strip()
    elif str(buyer.get("firstName") or "").strip():
        customer["name"]=" ".join(str(buyer.get(k) or "").strip() for k in ("lastName","firstName","middleName") if str(buyer.get(k) or "").strip())
    phone=str(buyer.get("phone") or buyer.get("replacementPhone") or "").strip()
    if phone:
        code=str(buyer.get("phoneCode") or "").strip()
        customer["phone"]=phone + ((" доб. "+code) if code else "")
    if customer:
        data["customer"]=customer

wa.call("shop.order.save",http_method="POST",data=data)

# Verify without printing PII.
after=wa.call("shop.order.getInfo",params={"id":oid})
ap=after.get("params") or {}
contact=after.get("contact") or {}
print("REPAIRED=1")
print("WB_ORDER_ID_MATCH="+("1" if str(ap.get("mp_wb_order_id") or "")==str(TARGET) else "0"))
print("ADDRESS_PRESENT="+("1" if bool(str(ap.get("shipping_address.street") or "").strip()) else "0"))
print("WB_CLIENT_AVAILABLE="+("1" if bool(buyer) else "0"))
print("NAME_PRESENT="+("1" if bool(str(contact.get("name") or "").strip()) and str(contact.get("name") or "").strip().lower()!="wildberries" else "0"))
print("PHONE_PRESENT="+("1" if bool(str(contact.get("phone") or "").strip()) else "0"))
