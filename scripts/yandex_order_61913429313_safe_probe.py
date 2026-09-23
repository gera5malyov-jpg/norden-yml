#!/usr/bin/env python3
from __future__ import annotations
import json, os, requests

ORDER="61913429313"
OUT=os.environ.get("SAFE_RESULT_PATH","yandex/order_61913429313_safe_probe.json")
TOKEN=os.environ["YANDEX_MARKET_API_KEY"].strip()
H={"Api-Key":TOKEN,"Accept":"application/json","Content-Type":"application/json"}

def s(v): return str(v or "").strip()

def unwrap(d):
    if isinstance(d,dict) and isinstance(d.get("order"),dict): return d["order"]
    if isinstance(d,dict) and isinstance(d.get("result"),dict):
        r=d["result"]
        if isinstance(r.get("order"),dict): return r["order"]
        return r
    return d if isinstance(d,dict) else {}

r=requests.get("https://api.partner.market.yandex.ru/v2/campaigns",headers=H,params={"limit":100},timeout=60)
r.raise_for_status()
d=r.json()
campaigns=d.get("campaigns") or (d.get("result") or {}).get("campaigns") or []
found=None; campaign_id=""
for c in campaigns:
    if not isinstance(c,dict): continue
    cid=s(c.get("id") or c.get("campaignId"))
    if not cid: continue
    rr=requests.get(f"https://api.partner.market.yandex.ru/v2/campaigns/{cid}/orders/{ORDER}",headers=H,timeout=60)
    if rr.status_code==200:
        found=unwrap(rr.json()); campaign_id=cid; break
if not found:
    raise RuntimeError("Заказ не найден ни в одной кампании")

buyer={}
rb=requests.get(f"https://api.partner.market.yandex.ru/v2/campaigns/{campaign_id}/orders/{ORDER}/buyer",headers=H,timeout=60)
if rb.ok:
    bd=rb.json()
    buyer=bd.get("result") if isinstance(bd,dict) and isinstance(bd.get("result"),dict) else bd if isinstance(bd,dict) else {}

delivery=found.get("delivery") if isinstance(found.get("delivery"),dict) else {}
address=delivery.get("address") if isinstance(delivery.get("address"),dict) else {}
dates=delivery.get("dates") if isinstance(delivery.get("dates"),dict) else {}
items=[]
for x in found.get("items") or []:
    if not isinstance(x,dict): continue
    items.append({
      "offer_id":s(x.get("offerId")),
      "count":x.get("count"),
      "name":s(x.get("offerName") or x.get("name")),
      "buyer_price":x.get("buyerPrice"),
    })
phone=s(buyer.get("phone"))
ext=s(buyer.get("phoneExtension"))
notes=s(delivery.get("notes") or found.get("notes") or found.get("buyerNotes"))
res={
  "ok":True,
  "order_number":ORDER,
  "campaign_id":campaign_id,
  "status":s(found.get("status")),
  "substatus":s(found.get("substatus")),
  "items":items,
  "delivery":{
    "type":s(delivery.get("type")),
    "service_name":s(delivery.get("serviceName")),
    "price":delivery.get("price"),
    "lift_price":delivery.get("liftPrice"),
    "lift_type":s(delivery.get("liftType")),
    "dates":dates,
    "city":s(address.get("city")),
    "region":s(address.get("region")),
    "floor":address.get("floor"),
  },
  "buyer":{
    "name_present":bool(s(buyer.get("firstName") or buyer.get("lastName") or buyer.get("name") or buyer.get("fullName"))),
    "phone_present":bool(phone),
    "phone_length":len(phone),
    "phone_extension_present":bool(ext),
    "phone_extension_length":len(ext),
  },
  "notes_present":bool(notes),
  "notes_length":len(notes),
}
os.makedirs(os.path.dirname(OUT),exist_ok=True)
with open(OUT,"w",encoding="utf-8") as f:
    json.dump(res,f,ensure_ascii=False,indent=2); f.write("\n")
print(json.dumps(res,ensure_ascii=False,indent=2))
