#!/usr/bin/env python3
import json, os
from datetime import datetime, timedelta, timezone
import requests, time

out={"generated_at":datetime.now(timezone.utc).isoformat()}

yt=os.environ.get("YANDEX_MARKET_API_KEY","").strip()
ys=requests.Session()
ys.headers.update({"Api-Key":yt,"Accept":"application/json","Content-Type":"application/json"})
r=ys.post("https://api.partner.market.yandex.ru/v1/businesses/20806099/orders",params={"limit":50},json={},timeout=60)
out["yandex_market"]={"http":r.status_code}
if r.ok:
    d=r.json()
    rows=(d.get("result") or {}).get("orders") or d.get("orders") or []
    out["yandex_market"]["count_page"]=len(rows)
    if rows:
        o=rows[0]
        out["yandex_market"]["order_fields"]=sorted(o.keys())
        item=(o.get("items") or [{}])[0] if o.get("items") else {}
        out["yandex_market"]["item_fields"]=sorted(item.keys()) if isinstance(item,dict) else []
        out["yandex_market"]["item_prices"]=item.get("prices") if isinstance(item,dict) else None
        out["yandex_market"]["order_prices"]=o.get("prices")
        out["yandex_market"]["delivery_fields"]=sorted((o.get("delivery") or {}).keys()) if isinstance(o.get("delivery"),dict) else []

kt=os.environ.get("YANDEX_KIT_TOKEN","").strip()
ks=requests.Session()
ks.headers.update({"Authorization":"Bearer "+kt,"Accept":"application/json"})
orders=[]; page=1; total=None
while True:
    rr=None
    for attempt in range(8):
        rr=ks.get("https://api.kit.yandex.net/v1/orders",params={"page":page,"per_page":100},timeout=60)
        if rr.status_code!=429:
            break
        time.sleep(3+attempt*2)
    if not rr.ok:
        out["yandex_kit"]={"http":rr.status_code,"error":rr.text[:500]}
        break
    d=rr.json()
    batch=[]
    if isinstance(d,list):
        batch=d
    elif isinstance(d,dict):
        for k in ("orders","items","results"):
            if isinstance(d.get(k),list):
                batch=d[k]; break
        total=d.get("total_count") or d.get("total") or ((d.get("meta") or {}).get("total_count") if isinstance(d.get("meta"),dict) else None)
    orders.extend(x for x in batch if isinstance(x,dict))
    if not batch or len(batch)<100 or (total is not None and len(orders)>=int(total)):
        break
    page+=1

if "yandex_kit" not in out:
    cutoff=datetime.now(timezone.utc)-timedelta(days=30)
    recent=[]
    for o in orders:
        try:
            dt=datetime.fromisoformat(str(o.get("created_at")).replace("Z","+00:00"))
        except Exception:
            continue
        if dt>=cutoff: recent.append(o)
    sample=recent[0] if recent else (orders[0] if orders else {})
    out["yandex_kit"]={"http":200,"total_orders":len(orders),"total_count":total,"recent_30d":len(recent),"order_fields":sorted(sample.keys()) if sample else []}
    if isinstance(sample,dict) and isinstance(sample.get("delivery_chunks"),list) and sample["delivery_chunks"]:
        ch=sample["delivery_chunks"][0]
        out["yandex_kit"]["list_chunk_fields"]=sorted(ch.keys()) if isinstance(ch,dict) else []
        if isinstance(ch,dict):
            for key in ("items","products","order_items","lines"):
                if isinstance(ch.get(key),list) and ch[key]:
                    out["yandex_kit"]["list_chunk_items_key"]=key
                    out["yandex_kit"]["list_chunk_item_fields"]=sorted(ch[key][0].keys())
                    break
            di=ch.get("delivery_info")
            if isinstance(di,dict):
                out["yandex_kit"]["list_delivery_info_fields"]=sorted(di.keys())
    if isinstance(sample,dict):
        client=sample.get("client")
        if isinstance(client,dict):
            out["yandex_kit"]["client_fields"]=sorted(client.keys())
        payment=sample.get("payment")
        if isinstance(payment,dict):
            out["yandex_kit"]["payment_fields"]=sorted(payment.keys())
    if sample and sample.get("id"):
        dd=ks.get("https://api.kit.yandex.net/v1/orders/"+str(sample["id"]),timeout=60)
        if dd.ok:
            detail=dd.json()
            out["yandex_kit"]["detail_fields"]=sorted(detail.keys()) if isinstance(detail,dict) else []
            for key in ("items","order_items","products","lines"):
                if isinstance(detail,dict) and isinstance(detail.get(key),list) and detail[key]:
                    out["yandex_kit"]["items_key"]=key
                    out["yandex_kit"]["item_fields"]=sorted(detail[key][0].keys())
                    break
            if isinstance(detail,dict) and isinstance(detail.get("delivery_chunks"),list) and detail["delivery_chunks"]:
                ch=detail["delivery_chunks"][0]
                out["yandex_kit"]["delivery_chunk_fields"]=sorted(ch.keys()) if isinstance(ch,dict) else []
                for key in ("items","products","order_items","lines"):
                    if isinstance(ch,dict) and isinstance(ch.get(key),list) and ch[key]:
                        out["yandex_kit"]["chunk_items_key"]=key
                        out["yandex_kit"]["chunk_item_fields"]=sorted(ch[key][0].keys())
                        break

print(json.dumps(out,ensure_ascii=False,indent=2))

# retry after KIT rate limit
