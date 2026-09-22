#!/usr/bin/env python3
import hashlib, os, re, sys, requests
from pathlib import Path

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE/"webasyst"))
from client import WebasystClient

TARGET=os.environ.get("TARGET_EXTERNAL_ID","61653679298").strip()
yt=os.environ["YANDEX_MARKET_API_KEY"].strip()
h={"Api-Key":yt,"Accept":"application/json","Content-Type":"application/json"}

def v(x):
    if isinstance(x,dict):
        return float(x.get("value") or 0)
    try:return float(x or 0)
    except:return 0.0

def digits(x):
    return re.sub(r"\D","",str(x or ""))

def ymd(x):
    t=str(x or "").strip()
    for fmt in ("%d-%m-%Y","%Y-%m-%d"):
        try:
            from datetime import datetime
            return datetime.strptime(t,fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return ""

# Find target in business-wide list.
r=requests.get("https://api.partner.market.yandex.ru/v2/campaigns",headers=h,params={"limit":100},timeout=60)
r.raise_for_status()
bids=[]
for c in r.json().get("campaigns") or []:
    b=c.get("business") or {}
    bid=b.get("id") or c.get("businessId")
    if bid and bid not in bids:bids.append(bid)
order=None
for bid in bids:
    rr=requests.post(f"https://api.partner.market.yandex.ru/v1/businesses/{bid}/orders",headers=h,params={"limit":50},json={},timeout=60)
    rr.raise_for_status()
    rows=((rr.json().get("result") or {}).get("orders") or rr.json().get("orders") or [])
    for o in rows:
        if str(o.get("orderId"))==TARGET:
            order=o;break
    if order:break
if not order:
    raise SystemExit("VERIFY_TARGET_NOT_FOUND=1")

cid=str(order.get("campaignId"))
oid=str(order.get("orderId"))
detail_r=requests.get(f"https://api.partner.market.yandex.ru/v2/campaigns/{cid}/orders/{oid}",headers=h,timeout=60)
detail_r.raise_for_status()
dd=detail_r.json()
detail=dd.get("order") if isinstance(dd.get("order"),dict) else dd.get("result") if isinstance(dd.get("result"),dict) else dd
buyer_r=requests.get(f"https://api.partner.market.yandex.ru/v2/campaigns/{cid}/orders/{oid}/buyer",headers=h,timeout=60)
buyer={}
if buyer_r.ok:
    bd=buyer_r.json()
    buyer=bd.get("result") if isinstance(bd.get("result"),dict) else bd if isinstance(bd,dict) else {}

delivery=detail.get("delivery") or order.get("delivery") or {}
dates=delivery.get("dates") or {}
delivery_price=float(delivery.get("price") or 0)
lift_price=float(delivery.get("liftPrice") or 0)
if not delivery_price:
    bd=((order.get("prices") or {}).get("delivery") or {})
    delivery_price=v(bd.get("payment"))+v(bd.get("subsidy"))
expected_shipping=delivery_price+lift_price
expected_date=ymd(dates.get("toDate") or dates.get("fromDate") or "")
lift_type=str(delivery.get("liftType") or "")

wa=WebasystClient(min_request_interval=0.2)
mp_key=hashlib.sha256(f"yandex_market|{TARGET}".encode()).hexdigest()[:32]
s=wa.call("shop.order.search",params={"hash":f"search/params.mp_key={mp_key}","limit":10,"fields":"*,contact_full,shipping_info"})
rows=s.get("orders") if isinstance(s,dict) else s
if not rows:
    raise SystemExit("VERIFY_WEBASYST_ORDER_NOT_FOUND=1")
info=wa.call("shop.order.getInfo",params={"id":rows[0]["id"]})
contact=info.get("contact") or {}
actual_name=str(contact.get("name") or "").strip()
actual_phone=str(contact.get("phone") or "").strip()
expected_name=" ".join(str(buyer.get(k) or "").strip() for k in ("lastName","firstName","middleName")).strip()
actual_shipping=float(info.get("shipping") or 0)
shipping_dt=str(info.get("shipping_datetime") or "")
params=info.get("params") or {}
acts=wa.call("shop.order.actions",params={"id":rows[0]["id"]})
allowed={str(x.get("id") or "") for x in (acts or []) if isinstance(x,dict)}

print("VERIFY_WEBASYST_ORDER_FOUND=1")
print("VERIFY_WEBASYST_STATE="+str(info.get("state_id") or ""))
print("VERIFY_EDITSHIPPINGDETAILS_ALLOWED="+("1" if "editshippingdetails" in allowed else "0"))
print("VERIFY_SHIPPING_DATETIME="+str(info.get("shipping_datetime") or ""))
print("VERIFY_YANDEX_STATUS="+str(order.get("status") or ""))
expected_tokens=sorted(x.lower() for x in expected_name.split() if x)
actual_tokens=sorted(x.lower() for x in actual_name.split() if x)
print("VERIFY_BUYER_NAME_MATCH="+("1" if expected_tokens and actual_tokens==expected_tokens else "0"))
print("VERIFY_BUYER_PHONE_MATCH="+("1" if buyer.get("phone") and digits(actual_phone)==digits(buyer.get("phone")) else "0"))
print(f"VERIFY_EXPECTED_DELIVERY_PRICE={delivery_price:.2f}")
print(f"VERIFY_EXPECTED_LIFT_PRICE={lift_price:.2f}")
print("VERIFY_EXPECTED_LIFT_TYPE="+lift_type)
print(f"VERIFY_EXPECTED_SHIPPING_TOTAL={expected_shipping:.2f}")
print(f"VERIFY_ACTUAL_SHIPPING_TOTAL={actual_shipping:.2f}")
print("VERIFY_SHIPPING_MATCH="+("1" if abs(actual_shipping-expected_shipping)<0.01 else "0"))
print("VERIFY_EXPECTED_DEADLINE="+expected_date)
print("VERIFY_DEADLINE_MATCH="+("1" if expected_date and shipping_dt.startswith(expected_date) else "0"))
print("VERIFY_LIFT_PARAM_PRESENT="+("1" if str(params.get("mp_lift_price") or "")!="" or lift_price==0 else "0"))
print("VERIFY_REVERSE_STATUS_WRITES=0")
