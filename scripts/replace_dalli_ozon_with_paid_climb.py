#!/usr/bin/env python3
from __future__ import annotations
import json, os, requests, xml.etree.ElementTree as ET

ORDER=os.environ.get("TARGET_EXTERNAL_ID","80399471-0026-1").strip()
OUT=os.environ.get("SAFE_RESULT_PATH",f"dalli/order_{ORDER}_paid_climb_replace.json")
OZON="https://api-seller.ozon.ru"
DALLI="https://api.dalli-service.com/v1/"

def s(v): return str(v or "").strip()
def num(v):
    try: return float(str(v or "0").replace(",","."))
    except: return 0.0

def ozon():
    r=requests.post(OZON+"/v3/posting/fbs/get",
      headers={"Client-Id":s(os.environ.get("OZON_CLIENT_ID")),"Api-Key":s(os.environ.get("OZON_API_KEY")),"Content-Type":"application/json"},
      json={"posting_number":ORDER,"with":{"analytics_data":False,"barcodes":False,"financial_data":False,"translit":False}},timeout=60)
    r.raise_for_status()
    d=r.json(); return d.get("result") if isinstance(d,dict) and isinstance(d.get("result"),dict) else d

def dalli(root):
    root.insert(0,ET.Element("auth",{"token":s(os.environ.get("DALLI_TOKEN_MSK"))}))
    r=requests.post(DALLI,data=ET.tostring(root,encoding="utf-8",xml_declaration=True),
      headers={"Content-Type":"application/xml; charset=utf-8","Accept":"application/xml, text/xml, */*"},timeout=60)
    r.raise_for_status()
    return ET.fromstring(r.content)

def getbasket():
    r=ET.Element("getbasket"); ET.SubElement(r,"number").text=ORDER
    return dalli(r)

def removebasket(barcode):
    r=ET.Element("removebasket")
    ET.SubElement(r,"barcode").text=barcode
    ET.SubElement(r,"number").text=ORDER
    x=dalli(r)
    errs=[(s(e.get("errorCode")),s(e.get("errorMessage"))) for e in x.findall(".//error")]
    if errs: raise RuntimeError("removebasket: "+"; ".join(m for _,m in errs))
    return x

def full_address(o):
    c=o.get("customer") if isinstance(o.get("customer"),dict) else {}
    a=c.get("address") if isinstance(c.get("address"),dict) else {}
    vals=[]
    for k in ("zip_code","country","region","city","address_tail"):
        v=s(a.get(k))
        if v and v not in vals: vals.append(v)
    return ", ".join(vals)

NOTE_KEYS=("comment","customer_comment","delivery_comment","recipient_comment","comment_to_delivery","order_comment","note","notes")
def customer_note(o):
    c=o.get("customer") if isinstance(o.get("customer"),dict) else {}
    a=c.get("address") if isinstance(c.get("address"),dict) else {}
    for scope in (o,c,a):
        for k in NOTE_KEYS:
            v=scope.get(k)
            if isinstance(v,str) and v.strip(): return v.strip()
    return ""

def interval(addr,service,target_date=""):
    r=ET.Element("intervals")
    ET.SubElement(r,"address").text=addr
    ET.SubElement(r,"service").text=service
    ET.SubElement(r,"strict").text="T"
    ET.SubElement(r,"output").text="dates"
    ET.SubElement(r,"format").text="minutes"
    x=dalli(r)
    rows=[]
    for d in x.findall(".//date"):
        date=s(d.get("value"))
        for iv in d.findall("./intervals/interval"):
            t1=s(iv.findtext("time_min")); t2=s(iv.findtext("time_max")); typ=s(iv.get("type"))
            if date and t1 and t2: rows.append((date,t1,t2,typ))
    if not rows: raise RuntimeError(f"Нет интервалов service {service}")
    if target_date:
        exact=[x for x in rows if x[0]==target_date]
        if exact: rows=exact
    basics=[x for x in rows if x[3].lower()=="basic"] or rows
    return basics[0][:3]

def create_order(o,service,date,t1,t2,with_climb):
    c=o.get("customer") if isinstance(o.get("customer"),dict) else {}
    ad=o.get("addressee") if isinstance(o.get("addressee"),dict) else {}
    person=s(ad.get("name") or c.get("name"))
    phone=s(ad.get("phone") or c.get("phone"))
    pin=s(ad.get("pin"))
    if pin: phone += f" доб. {pin}"
    products=[p for p in (o.get("products") or []) if isinstance(p,dict)]
    total=sum(num(p.get("price"))*max(1,int(p.get("quantity") or 1)) for p in products)
    prr=o.get("prr_option") if isinstance(o.get("prr_option"),dict) else {}
    root=ET.Element("basketcreate")
    n=ET.SubElement(root,"order",{"number":ORDER})
    rec=ET.SubElement(n,"receiver")
    for tag,val in (("address",full_address(o)),("person",person),("phone",phone),("date",date),("time_min",t1),("time_max",t2)):
        ET.SubElement(rec,tag).text=s(val)
    ET.SubElement(n,"service").text=service
    ET.SubElement(n,"quantity").text="1"
    ET.SubElement(n,"paytype").text="NO"
    ET.SubElement(n,"price").text="0"
    ET.SubElement(n,"inshprice").text=f"{total:.2f}"
    ET.SubElement(n,"instruction").text=customer_note(o)
    if with_climb:
        code=s(prr.get("code")).lower()
        typ="elevator" if code=="lift" else "stairs"
        floor=max(1,int(num(prr.get("floor"))))
        ads=ET.SubElement(n,"ads")
        ET.SubElement(ads,"climb",{"type":typ,"floor":str(floor)})
    items=ET.SubElement(n,"items")
    for p in products:
        q=max(1,int(p.get("quantity") or 1)); price=num(p.get("price"))
        it=ET.SubElement(items,"item",{"quantity":str(q),"retprice":f"{price:.2f}","inshprice":f"{price:.2f}","article":s(p.get("offer_id"))[:100],"VATrate":"0"})
        it.text=s(p.get("name"))[:250] or s(p.get("offer_id")) or "Товар Ozon"
    x=dalli(root)
    errs=[(s(e.get("errorCode")),s(e.get("errorMessage"))) for e in x.findall(".//error")]
    if errs: raise RuntimeError("basketcreate: "+"; ".join(m for _,m in errs))
    return x

def main():
    o=ozon()
    prr=o.get("prr_option") if isinstance(o.get("prr_option"),dict) else {}
    code=s(prr.get("code")).lower(); price=num(prr.get("price")); floor=int(num(prr.get("floor")))
    if price<=0 or code not in {"lift","stairs"}: raise RuntimeError("Нет отдельного оплаченного подъёма")
    cur=getbasket().find(".//order")
    if cur is None: raise RuntimeError("Исходный заказ не найден в корзине")
    old_barcode=s(cur.findtext("barcode"))
    addr=full_address(o)
    date,t1,t2=interval(addr,"30")
    removed=False
    try:
        removebasket(old_barcode); removed=True
        create_order(o,"30",date,t1,t2,True)
    except Exception as primary:
        if removed:
            try:
                rdate,rt1,rt2=interval(addr,"11")
                create_order(o,"11",rdate,rt1,rt2,False)
            except Exception as rollback:
                raise RuntimeError(f"ПРР не создан: {primary}; ОТКАТ ТОЖЕ НЕ УДАЛСЯ: {rollback}")
        raise
    v=getbasket().find(".//order")
    if v is None: raise RuntimeError("После пересоздания заказ не найден")
    climb=v.find("./ads/climb")
    result={
      "ok":True,"order_number":ORDER,"account":"МСК","in_basket":True,"sent_to_delivery":False,
      "recreated":True,"old_barcode":old_barcode,"barcode":s(v.findtext("barcode")),
      "service":s(v.findtext("service")),"date":s(v.findtext("./receiver/date")),
      "time_min":s(v.findtext("./receiver/time_min")),"time_max":s(v.findtext("./receiver/time_max")),
      "source_lift_price":price,"source_floor":floor,"source_lift_code":code,
      "climb_present":climb is not None,"climb_type":s(climb.get("type")) if climb is not None else "",
      "climb_floor":s(climb.get("floor")) if climb is not None else "",
      "vat_values":[s(i.get("VATrate")) for i in v.findall("./items/item")],"errors":[]
    }
    os.makedirs(os.path.dirname(OUT),exist_ok=True)
    with open(OUT,"w",encoding="utf-8") as f: json.dump(result,f,ensure_ascii=False,indent=2); f.write("\n")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if result["service"]!="30" or not result["climb_present"]: raise SystemExit(2)

if __name__=="__main__":
    try: main()
    except Exception as e:
        res={"ok":False,"order_number":ORDER,"account":"МСК","sent_to_delivery":False,"error":f"{type(e).__name__}: {e}"}
        os.makedirs(os.path.dirname(OUT),exist_ok=True)
        with open(OUT,"w",encoding="utf-8") as f: json.dump(res,f,ensure_ascii=False,indent=2); f.write("\n")
        print(json.dumps(res,ensure_ascii=False,indent=2))
        raise
