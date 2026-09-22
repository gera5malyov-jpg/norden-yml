#!/usr/bin/env python3
from __future__ import annotations

import hashlib, os, sys
from pathlib import Path
import requests

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from client import WebasystClient

TOKEN=os.environ.get("WB_API_TOKEN","").strip()
if not TOKEN:
    raise SystemExit("WB_API_TOKEN is not set")

H={"Authorization":TOKEN,"Accept":"application/json","Content-Type":"application/json"}
BASE="https://marketplace-api.wildberries.ru"
wa=WebasystClient(min_request_interval=0.25)

created=0
updated_address=0
updated_customer=0
skipped_sku=0
errors=0

def s(v):
    return str(v or "").strip()

def key(ext):
    return hashlib.sha256(f"wildberries|{ext}".encode()).hexdigest()[:32]

def wb_get(path):
    r=requests.get(BASE+path,headers=H,timeout=60)
    r.raise_for_status()
    return r.json()

def wb_post(path,body):
    r=requests.post(BASE+path,headers=H,json=body,timeout=60)
    r.raise_for_status()
    return r.json()

def wa_sku(code):
    p=wa.call("shop.product.search",params={"hash":f"search/query={code}","limit":100,"fields":"id,name,skus"})
    rows=p.get("products") if isinstance(p,dict) else p if isinstance(p,list) else []
    found=[]
    for prod in rows or []:
        skus=prod.get("skus") or {}
        skus=list(skus.values()) if isinstance(skus,dict) else skus if isinstance(skus,list) else []
        for x in skus:
            if isinstance(x,dict) and s(x.get("sku"))==code and s(x.get("id")):
                found.append({"sku_id":s(x.get("id")),"product_id":s(prod.get("id"))})
    uniq={(x["sku_id"],x["product_id"]):x for x in found}
    return next(iter(uniq.values())) if len(uniq)==1 else None

def search_param(param,value):
    if not s(value):
        return None
    p=wa.call("shop.order.search",params={"hash":f"search/params.{param}={s(value)}","limit":10,"fields":"*,state"})
    rows=p.get("orders") if isinstance(p,dict) else p if isinstance(p,list) else []
    rows=[x for x in (rows or []) if isinstance(x,dict)]
    if len(rows)>1:
        raise RuntimeError(f"duplicate {param}")
    return rows[0] if rows else None

def save_address(oid,order):
    global updated_address
    addr=order.get("address") if isinstance(order.get("address"),dict) else {}
    full=s(addr.get("fullAddress"))
    if not full:
        return
    info=wa.call("shop.order.getInfo",params={"id":oid})
    params=dict(info.get("params") or {})
    params["shipping_address.street"]=full
    params["mp_wb_full_address"]=full
    if addr.get("latitude") is not None:
        params["mp_wb_latitude"]=s(addr.get("latitude"))
    if addr.get("longitude") is not None:
        params["mp_wb_longitude"]=s(addr.get("longitude"))
    wa.call("shop.order.save",http_method="POST",data={
        "id":oid,
        "params":params,
        "shipping_address":{"street":full},
    })
    updated_address+=1

def create_new(order):
    global created, skipped_sku
    oid_wb=s(order.get("id"))
    rid=s(order.get("rid"))
    old=search_param("mp_wb_order_id",oid_wb) or search_param("mp_wb_rid",rid)
    if old:
        save_address(s(old.get("id")),order)
        return s(old.get("id"))

    article=s(order.get("article"))
    sku=wa_sku(article)
    if not sku:
        skipped_sku+=1
        return ""

    raw=order.get("convertedFinalPrice")
    if raw in (None,""):
        raw=order.get("finalPrice")
    try:
        price=float(raw or 0)/100.0
    except Exception:
        price=0.0

    params={
        "mp_key":key(oid_wb),
        "mp_source":"wildberries",
        "mp_external_id":oid_wb,
        "mp_wb_order_id":oid_wb,
        "mp_wb_rid":rid,
        "mp_wb_order_uid":s(order.get("orderUid")),
        "mp_wb_delivery_type":s(order.get("deliveryType")),
        "mp_wb_live_capture":"1",
        "shipping_name":"Wildberries",
        "payment_name":"Wildberries",
    }
    addr=order.get("address") if isinstance(order.get("address"),dict) else {}
    full=s(addr.get("fullAddress"))
    if full:
        params["shipping_address.street"]=full
        params["mp_wb_full_address"]=full

    created_order=wa.call("shop.order.add",http_method="POST",data={
        "items":[{"sku_id":sku["sku_id"],"quantity":1}],
        "contact":{"name":"Wildberries"},
        "comment":f"Импортировано из Wildberries. Номер заказа WB: {oid_wb}. Статусы обратно в Wildberries не передаются.",
        "params":params,
    })
    oid=s(created_order.get("id")) if isinstance(created_order,dict) else ""
    if not oid:
        raise RuntimeError("Webasyst did not return order id")

    ci=(created_order.get("items") or [{}])[0] if isinstance(created_order,dict) else {}
    save_item={
        "item_id":s(ci.get("id")),
        "product_id":sku["product_id"],
        "sku_id":sku["sku_id"],
        "quantity":1,
    }
    if price>0:
        save_item["price"]=f"{price:.2f}"
    data={"id":oid,"items":[save_item],"params":params}
    if full:
        data["shipping_address"]={"street":full}
    wa.call("shop.order.save",http_method="POST",data=data)
    created+=1
    return oid

# 1. Snapshot new DBS orders immediately, before WB later removes them from the new list.
new_payload=wb_get("/api/v3/dbs/orders/new")
new_orders=[x for x in (new_payload.get("orders") or []) if isinstance(x,dict)]
known={}
for order in new_orders:
    try:
        oid=create_new(order)
        if oid and s(order.get("id")):
            known[s(order.get("id"))]=oid
    except Exception:
        errors+=1

# 2. Load recent WB orders already known to Webasyst; numeric WB IDs let us request
# buyer information after the seller has moved the order to assembly/delivery.
try:
    p=wa.call("shop.order.search",params={"hash":"search/params.mp_source=wildberries","limit":500,"fields":"*,state"})
    rows=p.get("orders") if isinstance(p,dict) else p if isinstance(p,list) else []
except Exception:
    rows=[]

for row in rows or []:
    try:
        oid=s(row.get("id"))
        if not oid:
            continue
        info=wa.call("shop.order.getInfo",params={"id":oid})
        params=info.get("params") or {}
        wb_id=s(params.get("mp_wb_order_id"))
        if wb_id and s(params.get("mp_wb_live_capture"))=="1":
            known[wb_id]=oid
    except Exception:
        errors+=1

# 3. Buyer data is available only during the active DBS lifecycle. Poll it frequently
# and write it straight to Webasyst; no PII is written to GitHub or logs.
ids=[]
for x in known:
    try:
        ids.append(int(x))
    except Exception:
        pass

for i in range(0,len(ids),100):
    chunk=ids[i:i+100]
    if not chunk:
        continue
    try:
        payload=wb_post("/api/v3/dbs/orders/client",{"orders":chunk})
    except Exception:
        errors+=1
        continue
    for buyer in (payload.get("orders") or []):
        if not isinstance(buyer,dict):
            continue
        wb_id=s(buyer.get("orderID"))
        oid=known.get(wb_id)
        if not oid:
            continue
        name=s(buyer.get("fullName"))
        if not name:
            name=" ".join(x for x in (s(buyer.get("lastName")),s(buyer.get("firstName")),s(buyer.get("middleName"))) if x).strip()
        replacement=s(buyer.get("replacementPhone"))
        phone=replacement or s(buyer.get("phone"))
        if phone and not replacement and s(buyer.get("phoneCode")):
            phone=phone+" доб. "+s(buyer.get("phoneCode"))
        customer={}
        if name:
            customer["name"]=name
        if phone:
            customer["phone"]=phone
        if customer:
            try:
                wa.call("shop.order.save",http_method="POST",data={"id":oid,"customer":customer})
                updated_customer+=1
            except Exception:
                errors+=1

print({
    "new_dbs_read":len(new_orders),
    "created":created,
    "address_updates":updated_address,
    "customer_updates":updated_customer,
    "skipped_unmatched_sku":skipped_sku,
    "errors":errors,
    "reverse_status_writes":0,
})
raise SystemExit(2 if errors else 0)
