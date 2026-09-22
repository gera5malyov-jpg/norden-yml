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

# Resolve the article to Webasyst SKU ids for reliable matching.
sku_ids=set()
if article:
    ps=wa.call("shop.product.search",params={"hash":f"search/query={article}","limit":100,"fields":"id,name,skus"})
    prows=ps.get("products") if isinstance(ps,dict) else ps if isinstance(ps,list) else []
    for p in prows or []:
        skus=p.get("skus") or {}
        skus=list(skus.values()) if isinstance(skus,dict) else skus if isinstance(skus,list) else []
        for sk in skus:
            if isinstance(sk,dict) and str(sk.get("sku") or "").strip()==article:
                sku_ids.add(str(sk.get("id") or ""))

# Find the existing WB order in Webasyst. Historical importer used gNumber, so match
# by source + exact article/SKU + nearest source creation date rather than creating a duplicate.
sr=wa.call("shop.order.search",params={
    "hash":"search/params.mp_source=wildberries",
    "limit":100,
    "fields":"*,state"
})
rows=sr.get("orders") if isinstance(sr,dict) else sr if isinstance(sr,list) else []
# Fallback for orders created by older importers without mp_source.
if not rows and article:
    alt=wa.call("shop.order.search",params={"hash":f"search/query={article}","limit":100,"fields":"*,state"})
    rows=alt.get("orders") if isinstance(alt,dict) else alt if isinstance(alt,list) else []
# Exact WB order number may also be present in a comment/parameter from an older importer.
if not rows:
    alt=wa.call("shop.order.search",params={"hash":f"search/query={TARGET}","limit":100,"fields":"*,state"})
    rows=alt.get("orders") if isinstance(alt,dict) else alt if isinstance(alt,list) else []
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
    order_sku_ids={str(x.get("sku_id") or "").strip() for x in items if isinstance(x,dict)}
    if article and article not in sku_codes and not (sku_ids & order_sku_ids):
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

if not candidates and len(rows)==1 and target_dt:
    oid=str(rows[0].get("id") or "")
    if oid:
        info=wa.call("shop.order.getInfo",params={"id":oid})
        p=info.get("params") or {}
        mp_created=str(p.get("mp_created_at") or "")
        try:
            wd=datetime.fromisoformat(mp_created.replace("Z","+00:00"))
            delta=abs((wd-target_dt).total_seconds())
        except Exception:
            delta=999999999
        # Only one historical WB order exists in Webasyst and its source timestamp is
        # within 6 hours of the Marketplace DBS order. Treat it as the same order.
        if delta <= 6*3600:
            candidates.append((delta,oid,info))

if not candidates:
    print("DIAG_TARGET_ARTICLE="+article)
    print("DIAG_TARGET_CREATED="+created)
    print("DIAG_SOURCE_ROWS="+str(len(rows)))
    for idx,row in enumerate(rows[:20]):
        oid=str(row.get("id") or "")
        if not oid: continue
        info=wa.call("shop.order.getInfo",params={"id":oid})
        p=info.get("params") or {}
        its=info.get("items") or []
        codes=[str(x.get("sku_code") or x.get("sku") or "") for x in its if isinstance(x,dict)]
        ids=[str(x.get("sku_id") or "") for x in its if isinstance(x,dict)]
        print(f"DIAG_ROW_{idx}_ORDER_ID="+oid)
        print(f"DIAG_ROW_{idx}_MP_EXTERNAL_ID="+str(p.get("mp_external_id") or ""))
        print(f"DIAG_ROW_{idx}_MP_CREATED="+str(p.get("mp_created_at") or ""))
        print(f"DIAG_ROW_{idx}_SKU_CODES="+",".join(codes))
        print(f"DIAG_ROW_{idx}_SKU_IDS="+",".join(ids))
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
