#!/usr/bin/env python3
from __future__ import annotations
import json, os, requests, xml.etree.ElementTree as ET
from datetime import datetime

ORDER=os.environ.get("TARGET_EXTERNAL_ID","61913429313").strip()
CAMPAIGN=os.environ.get("YANDEX_CAMPAIGN_ID","93424614").strip()
OUT=os.environ.get("SAFE_RESULT_PATH",f"dalli/yandex_order_{ORDER}_msk_basket_result.json")
YM="https://api.partner.market.yandex.ru"
DALLI="https://api.dalli-service.com/v1/"

def s(v): return str(v or "").strip()
def num(v):
    try: return float(str(v or "0").replace(",","."))
    except: return 0.0

def yget(path):
    h={"Api-Key":s(os.environ.get("YANDEX_MARKET_API_KEY")),"Accept":"application/json","Content-Type":"application/json"}
    r=requests.get(YM+path,headers=h,timeout=60)
    if not r.ok: raise RuntimeError(f"Yandex HTTP {r.status_code}: {r.text[:300]}")
    return r.json()

def unwrap(d):
    if isinstance(d,dict) and isinstance(d.get("order"),dict): return d["order"]
    if isinstance(d,dict) and isinstance(d.get("result"),dict):
        r=d["result"]
        if isinstance(r.get("order"),dict): return r["order"]
        return r
    return d if isinstance(d,dict) else {}

def order():
    return unwrap(yget(f"/v2/campaigns/{CAMPAIGN}/orders/{ORDER}"))

def buyer():
    return unwrap(yget(f"/v2/campaigns/{CAMPAIGN}/orders/{ORDER}/buyer"))

def dalli(root):
    token=s(os.environ.get("DALLI_TOKEN_MSK"))
    if not token: raise RuntimeError("DALLI_TOKEN_MSK не задан")
    root.insert(0,ET.Element("auth",{"token":token}))
    r=requests.post(DALLI,data=ET.tostring(root,encoding="utf-8",xml_declaration=True),
      headers={"Content-Type":"application/xml; charset=utf-8","Accept":"application/xml, text/xml, */*"},timeout=60)
    if not r.ok: raise RuntimeError(f"Dalli HTTP {r.status_code}: {r.text[:300]}")
    return ET.fromstring(r.content)

def getbasket():
    root=ET.Element("getbasket"); ET.SubElement(root,"number").text=ORDER
    return dalli(root)

def full_address(delivery):
    a=delivery.get("address") if isinstance(delivery.get("address"),dict) else {}
    if s(a.get("fullAddress")):
        base=s(a.get("fullAddress"))
        extra=[]
        if s(a.get("entrance")): extra.append("подъезд "+s(a.get("entrance")))
        if s(a.get("apartment")): extra.append("кв./офис "+s(a.get("apartment")))
        return ", ".join([base]+extra)
    vals=[]
    for k in ("postcode","country","region","city","street"):
        v=s(a.get(k))
        if v and v not in vals: vals.append(v)
    for label,key in (("д.","house"),("корп.","building"),("стр.","block"),("подъезд","entrance"),("кв./офис","apartment")):
        v=s(a.get(key))
        if v: vals.append(f"{label} {v}")
    return ", ".join(vals)

def target_date(delivery):
    dates=delivery.get("dates") if isinstance(delivery.get("dates"),dict) else {}
    raw=s(dates.get("toDate") or dates.get("fromDate"))
    if not raw: return ""
    for fmt in ("%d-%m-%Y","%Y-%m-%d"):
        try: return datetime.strptime(raw,fmt).strftime("%Y-%m-%d")
        except: pass
    return ""

def get_interval(addr,service,target=""):
    root=ET.Element("intervals")
    ET.SubElement(root,"address").text=addr
    ET.SubElement(root,"service").text=service
    ET.SubElement(root,"strict").text="T"
    ET.SubElement(root,"output").text="dates"
    ET.SubElement(root,"format").text="minutes"
    x=dalli(root)
    rows=[]
    for d in x.findall(".//date"):
        date=s(d.get("value"))
        for iv in d.findall("./intervals/interval"):
            t1=s(iv.findtext("time_min")); t2=s(iv.findtext("time_max")); typ=s(iv.get("type"))
            if date and t1 and t2: rows.append((date,t1,t2,typ))
    if not rows: raise RuntimeError(f"Dalli не вернул доступных интервалов для service {service}")
    if target:
        exact=[r for r in rows if r[0]==target]
        if exact: rows=exact
    basic=[r for r in rows if r[3].lower()=="basic"] or rows
    return basic[0][0],basic[0][1],basic[0][2],(basic[0][0]==target if target else True)

def buyer_name(b):
    full=s(b.get("name") or b.get("fullName"))
    if full: return full
    return " ".join(x for x in (s(b.get("lastName")),s(b.get("firstName")),s(b.get("middleName"))) if x)

def note(o,delivery):
    for v in (delivery.get("notes"),o.get("notes"),o.get("buyerNotes"),o.get("comment")):
        if isinstance(v,str) and v.strip(): return v.strip()
    return ""

def main():
    o=order(); b=buyer()
    delivery=o.get("delivery") if isinstance(o.get("delivery"),dict) else {}
    addr=full_address(delivery)
    person=buyer_name(b)
    phone=s(b.get("phone"))
    ext=s(b.get("phoneExtension"))
    if ext: phone += f" доб. {ext}"
    if not addr or not person or not phone: raise RuntimeError("Яндекс не вернул полный адрес/ФИО/телефон")

    payment_type=s(o.get("paymentType")).upper()
    payment_method=s(o.get("paymentMethod")).upper()
    if payment_type!="PREPAID":
        raise RuntimeError(f"Неподдержанный способ оплаты без ручной проверки: {payment_type}/{payment_method}")

    lift_price=num(delivery.get("liftPrice"))
    lift_type=s(delivery.get("liftType")).upper()
    a=delivery.get("address") if isinstance(delivery.get("address"),dict) else {}
    try: floor=max(1,int(float(str(a.get("floor") or "1").replace(",","."))))
    except: floor=1
    paid_lift=lift_price>0 and lift_type not in {"","NOT_NEEDED","FREE","UNKNOWN"}
    climb_type="stairs" if lift_type=="MANUAL" else "elevator"

    target=target_date(delivery)
    if paid_lift:
        service=""
        last_error=None
        for candidate in ("30","32"):
            try:
                date,t1,t2,target_matched=get_interval(addr,candidate,target)
                service=candidate
                break
            except Exception as exc:
                last_error=exc
        if not service:
            # Для КГТ Dalli может не публиковать интервалы, но basketcreate принимает дату.
            # Сохраняем дату Яндекс без самовольного переноса и используем базовый интервал.
            if not target:
                raise RuntimeError(f"Dalli не вернул КГТ-интервалы и у Яндекс нет даты доставки: {last_error}")
            service="30"
            date,t1,t2,target_matched=target,"10:00","22:00",True
    else:
        service="11"
        date,t1,t2,target_matched=get_interval(addr,service,target)

    existing=getbasket().find(".//order")
    if existing is not None:
        cl=existing.find("./ads/climb")
        res={"ok":True,"already_existed":True,"order_number":ORDER,"account":"МСК","in_basket":True,"sent_to_delivery":False,
             "barcode":s(existing.findtext("barcode")),"service":s(existing.findtext("service")),
             "climb_present":cl is not None,"climb_type":s(cl.get("type")) if cl is not None else "",
             "climb_floor":s(cl.get("floor")) if cl is not None else ""}
        os.makedirs(os.path.dirname(OUT),exist_ok=True)
        with open(OUT,"w",encoding="utf-8") as f: json.dump(res,f,ensure_ascii=False,indent=2); f.write("\n")
        print(json.dumps(res,ensure_ascii=False,indent=2)); return

    items=[x for x in (o.get("items") or []) if isinstance(x,dict)]
    if not items: raise RuntimeError("В заказе Яндекс нет товаров")
    total=0.0
    for x in items:
        q=max(1,int(x.get("count") or 1)); p=num(x.get("buyerPrice"))
        total += q*p

    root=ET.Element("basketcreate")
    n=ET.SubElement(root,"order",{"number":ORDER})
    rec=ET.SubElement(n,"receiver")
    for tag,val in (("address",addr),("person",person),("phone",phone),("date",date),("time_min",t1),("time_max",t2)):
        ET.SubElement(rec,tag).text=s(val)
    ET.SubElement(n,"service").text=service
    ET.SubElement(n,"quantity").text="1"
    ET.SubElement(n,"paytype").text="NO"
    ET.SubElement(n,"price").text="0"
    ET.SubElement(n,"inshprice").text=f"{total:.2f}"
    ET.SubElement(n,"instruction").text=note(o,delivery)
    if paid_lift:
        ads=ET.SubElement(n,"ads")
        ET.SubElement(ads,"climb",{"type":climb_type,"floor":str(floor)})
    its=ET.SubElement(n,"items")
    for x in items:
        q=max(1,int(x.get("count") or 1)); p=num(x.get("buyerPrice"))
        art=s(x.get("offerId"))
        name=s(x.get("offerName") or x.get("name") or art or "Товар Яндекс")
        it=ET.SubElement(its,"item",{"quantity":str(q),"retprice":f"{p:.2f}","inshprice":f"{p:.2f}","article":art[:100],"VATrate":"0"})
        it.text=name[:250]

    resp=dalli(root)
    errs=[{"code":s(e.get("errorCode")),"field":s(e.get("error")),"message":s(e.get("errorMessage"))} for e in resp.findall(".//error")]
    if errs: raise RuntimeError("Dalli basketcreate: "+"; ".join(x["message"] for x in errs))

    v=getbasket().find(".//order")
    if v is None: raise RuntimeError("Dalli не подтвердил заказ в корзине")
    cl=v.find("./ads/climb")
    res={
      "ok":True,"already_existed":False,"order_number":ORDER,"account":"МСК","in_basket":True,"sent_to_delivery":False,
      "barcode":s(v.findtext("barcode")),"service":s(v.findtext("service")),
      "date":s(v.findtext("./receiver/date")),"time_min":s(v.findtext("./receiver/time_min")),"time_max":s(v.findtext("./receiver/time_max")),
      "yandex_target_date":target,"target_date_matched":target_matched,
      "payment_type":payment_type,"payment_method":payment_method,
      "source_lift_price":lift_price,"source_lift_type":lift_type,"source_floor":floor,
      "climb_present":cl is not None,"climb_type":s(cl.get("type")) if cl is not None else "",
      "climb_floor":s(cl.get("floor")) if cl is not None else "",
      "note_present":bool(note(o,delivery)),"vat_values":[s(i.get("VATrate")) for i in v.findall("./items/item")],
      "errors":[]
    }
    os.makedirs(os.path.dirname(OUT),exist_ok=True)
    with open(OUT,"w",encoding="utf-8") as f: json.dump(res,f,ensure_ascii=False,indent=2); f.write("\n")
    print(json.dumps(res,ensure_ascii=False,indent=2))
    if paid_lift and (service not in {"30","32"} or cl is None): raise SystemExit(2)

if __name__=="__main__":
    try: main()
    except Exception as e:
        res={"ok":False,"order_number":ORDER,"account":"МСК","in_basket":False,"sent_to_delivery":False,"error":f"{type(e).__name__}: {e}"}
        os.makedirs(os.path.dirname(OUT),exist_ok=True)
        with open(OUT,"w",encoding="utf-8") as f: json.dump(res,f,ensure_ascii=False,indent=2); f.write("\n")
        print(json.dumps(res,ensure_ascii=False,indent=2)); raise
