#!/usr/bin/env python3
from __future__ import annotations
import json, os, time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webasyst"))
from client import WebasystClient

NOW=datetime.now(timezone.utc)
OUT=Path("marketplace_order_sync_audit.json")
def iso(dt): return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
def date(dt): return dt.strftime("%d-%m-%Y")
def s(v): return str(v or "").strip()
def listify(payload, keys=()):
    if isinstance(payload,list): return [x for x in payload if isinstance(x,dict)]
    if isinstance(payload,dict):
        for k in keys:
            v=payload.get(k)
            if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
            if isinstance(v,dict): return [x for x in v.values() if isinstance(x,dict)]
        if payload and all(isinstance(v,dict) for v in payload.values()): return list(payload.values())
    return []

report={"generated_at":iso(NOW),"yandex":{},"wildberries":{},"ozon":{},"webasyst":{}}

# Webasyst SKU index
wa=WebasystClient(min_request_interval=0.25)
sku_map={}
offset=0
while True:
    p=wa.call("shop.product.search",params={"offset":offset,"limit":1000,"fields":"id,name,skus"})
    batch=listify(p,("products","items"))
    for prod in batch:
        skus=prod.get("skus") or {}
        rows=list(skus.values()) if isinstance(skus,dict) else skus if isinstance(skus,list) else []
        for row in rows:
            if not isinstance(row,dict): continue
            code=s(row.get("sku"))
            if code:
                sku_map.setdefault(code,[]).append({"sku_id":s(row.get("id")),"product_id":s(prod.get("id"))})
    if len(batch)<1000: break
    offset += len(batch)
report["webasyst"]["sku_count"]=len(sku_map)
settings=wa.call("shop.settings.get")
states=settings.get("order_states") if isinstance(settings,dict) else []
if isinstance(states,dict): states=list(states.values())
report["webasyst"]["states"]=[{"id":x.get("id"),"name":x.get("name")} for x in (states or []) if isinstance(x,dict)]

# One action sample per state, sanitized
actions_by_state={}
for st in [x.get("id") for x in (states or []) if isinstance(x,dict) and x.get("id")]:
    try:
        p=wa.call("shop.order.search",params={"hash":f"search/state_id={st}","limit":1})
        rows=listify(p,("orders","items"))
        if not rows: continue
        oid=rows[0].get("id")
        acts=wa.call("shop.order.actions",params={"id":oid})
        acts_rows=listify(acts,("actions","items"))
        actions_by_state[st]=[{"id":a.get("id"),"name":a.get("name")} for a in acts_rows]
    except Exception as e:
        actions_by_state[st]=[{"error":str(e)[:300]}]
report["webasyst"]["actions_by_state"]=actions_by_state

# Yandex Market
ytoken=os.getenv("YANDEX_MARKET_API_KEY","").strip()
ys=requests.Session(); ys.headers.update({"Api-Key":ytoken,"Accept":"application/json"})
r=ys.get("https://api.partner.market.yandex.ru/v2/campaigns",params={"limit":100},timeout=60)
report["yandex"]["campaigns_http"]=r.status_code
campaigns=[]
if r.ok:
    payload=r.json()
    campaigns=listify(payload,("campaigns",))
    # common shape campaigns under payload
    if not campaigns and isinstance(payload,dict):
        c=payload.get("campaigns")
        if isinstance(c,list): campaigns=c
    report["yandex"]["campaigns"]=[{"id":c.get("id"),"domain":c.get("domain"),"placementType":c.get("placementType"),"business_id":(c.get("business") or {}).get("id") if isinstance(c.get("business"),dict) else c.get("businessId")} for c in campaigns]
else:
    report["yandex"]["error"]=r.text[:500]

y_skus=set(); y_status=Counter(); y_count=0
from_d=date(NOW-timedelta(days=30)); to_d=date(NOW)
for c in campaigns:
    cid=c.get("id")
    if not cid: continue
    token=None
    while True:
        params={"fromDate":from_d,"toDate":to_d,"limit":50}
        if token: params["pageToken"]=token
        rr=ys.get(f"https://api.partner.market.yandex.ru/v2/campaigns/{cid}/orders",params=params,timeout=60)
        if not rr.ok:
            report["yandex"].setdefault("order_errors",[]).append({"campaign_id":cid,"http":rr.status_code,"text":rr.text[:500]})
            break
        data=rr.json()
        orders=listify(data,("orders",))
        if isinstance(data,dict) and isinstance(data.get("orders"),list): orders=data["orders"]
        for o in orders:
            y_count+=1; y_status[s(o.get("status"))]+=1
            for item in o.get("items") or []:
                if not isinstance(item,dict): continue
                code=s(item.get("offerId") or item.get("shopSku") or item.get("offerName"))
                if code: y_skus.add(code)
        paging=data.get("paging") if isinstance(data,dict) else None
        token=(paging or {}).get("nextPageToken") if isinstance(paging,dict) else data.get("nextPageToken") if isinstance(data,dict) else None
        if not token: break
report["yandex"].update({
    "period_days":30,"orders":y_count,"statuses":dict(y_status),
    "unique_item_codes":len(y_skus),
    "matched_item_codes":sum(1 for x in y_skus if x in sku_map),
    "unmatched_sample":sorted([x for x in y_skus if x not in sku_map])[:30],
})

# Wildberries FBS, two windows of <=30 days
wtoken=os.getenv("WB_API_TOKEN","").strip()
wh={"Authorization":wtoken,"Accept":"application/json"}
wb_orders={}
for start_days,end_days in [(60,30),(30,0)]:
    start=int((NOW-timedelta(days=start_days)).timestamp())
    end=int((NOW-timedelta(days=end_days)).timestamp())
    nxt=0
    while True:
        rr=requests.get("https://marketplace-api.wildberries.ru/api/v3/orders",headers=wh,params={"limit":1000,"next":nxt,"dateFrom":start,"dateTo":end},timeout=60)
        if not rr.ok:
            report["wildberries"].setdefault("fbs_errors",[]).append({"http":rr.status_code,"text":rr.text[:500],"window":[start_days,end_days]})
            break
        data=rr.json()
        rows=data.get("orders") or []
        for o in rows:
            wb_orders[str(o.get("id"))]=o
        new_next=data.get("next",0)
        if not rows or not new_next or new_next==nxt: break
        nxt=new_next

wb_ids=[int(x) for x in wb_orders.keys() if x.isdigit()]
wb_status={}
for i in range(0,len(wb_ids),1000):
    rr=requests.post("https://marketplace-api.wildberries.ru/api/v3/orders/status",headers={**wh,"Content-Type":"application/json"},json={"orders":wb_ids[i:i+1000]},timeout=60)
    if rr.ok:
        for x in (rr.json().get("orders") or []): wb_status[str(x.get("id"))]=x
    else:
        report["wildberries"].setdefault("status_errors",[]).append({"http":rr.status_code,"text":rr.text[:500]})
        break
wb_skus=set()
for o in wb_orders.values():
    for k in ("article","supplierArticle","vendorCode"):
        if s(o.get(k)): wb_skus.add(s(o.get(k)))
report["wildberries"]["fbs"]={
    "orders":len(wb_orders),
    "supplier_statuses":dict(Counter(s(x.get("supplierStatus")) for x in wb_status.values())),
    "wb_statuses":dict(Counter(s(x.get("wbStatus")) for x in wb_status.values())),
    "unique_item_codes":len(wb_skus),
    "matched_item_codes":sum(1 for x in wb_skus if x in sku_map),
    "unmatched_sample":sorted([x for x in wb_skus if x not in sku_map])[:30],
    "sample_fields":sorted(list(next(iter(wb_orders.values())).keys())) if wb_orders else []
}

# WB statistics all-order feed permission/check for 60 days
rr=requests.get("https://statistics-api.wildberries.ru/api/v1/supplier/orders",headers=wh,params={"dateFrom":(NOW-timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S"),"flag":0},timeout=90)
report["wildberries"]["statistics_http"]=rr.status_code
if rr.ok:
    rows=rr.json() if isinstance(rr.json(),list) else []
    codes={s(x.get("supplierArticle")) for x in rows if s(x.get("supplierArticle"))}
    report["wildberries"]["statistics"]={
      "rows":len(rows),"cancelled":sum(1 for x in rows if x.get("isCancel")),
      "unique_item_codes":len(codes),"matched_item_codes":sum(1 for x in codes if x in sku_map),
      "unmatched_sample":sorted([x for x in codes if x not in sku_map])[:30],
      "sample_fields":sorted(list(rows[0].keys())) if rows else []
    }
else:
    report["wildberries"]["statistics_error"]=rr.text[:500]

# Ozon readiness only; no historical import
report["ozon"]={"historical_import":False,"cutoff_utc":iso(NOW),"new_orders_only":True}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(report,ensure_ascii=False,indent=2))
