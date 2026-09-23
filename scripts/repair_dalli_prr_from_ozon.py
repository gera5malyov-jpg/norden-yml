#!/usr/bin/env python3
from __future__ import annotations
import json, os, requests, xml.etree.ElementTree as ET

OZON="https://api-seller.ozon.ru"
DALLI="https://api.dalli-service.com/v1/"
ORDER=os.environ["TARGET_EXTERNAL_ID"].strip()
OUT=os.environ.get("SAFE_RESULT_PATH",f"dalli/order_{ORDER}_prr_repair.json")

def s(v): return str(v or "").strip()

def ozon_order():
    r=requests.post(
        OZON+"/v3/posting/fbs/get",
        headers={"Client-Id":s(os.environ.get("OZON_CLIENT_ID")),"Api-Key":s(os.environ.get("OZON_API_KEY")),"Content-Type":"application/json"},
        json={"posting_number":ORDER,"with":{"analytics_data":False,"barcodes":False,"financial_data":False,"translit":False}},
        timeout=60)
    r.raise_for_status()
    d=r.json()
    return d.get("result") if isinstance(d,dict) and isinstance(d.get("result"),dict) else d

def dalli(root):
    root.insert(0,ET.Element("auth",{"token":s(os.environ.get("DALLI_TOKEN_MSK"))}))
    r=requests.post(DALLI,data=ET.tostring(root,encoding="utf-8",xml_declaration=True),
                    headers={"Content-Type":"application/xml; charset=utf-8","Accept":"application/xml, text/xml, */*"},timeout=60)
    r.raise_for_status()
    return ET.fromstring(r.content)

def getbasket():
    root=ET.Element("getbasket"); ET.SubElement(root,"number").text=ORDER
    return dalli(root)

def get_interval(addr, service="30"):
    root=ET.Element("intervals")
    ET.SubElement(root,"address").text=addr
    ET.SubElement(root,"service").text=service
    ET.SubElement(root,"strict").text="T"
    ET.SubElement(root,"output").text="dates"
    ET.SubElement(root,"format").text="minutes"
    x=dalli(root)
    dates=[]
    for d in x.findall(".//date"):
        val=s(d.get("value"))
        for iv in d.findall("./intervals/interval"):
            t1=s(iv.findtext("time_min")); t2=s(iv.findtext("time_max")); typ=s(iv.get("type"))
            if val and t1 and t2: dates.append((val,t1,t2,typ))
    if not dates: raise RuntimeError("Dalli не вернул интервал для service 30")
    dates.sort(key=lambda x:x[0])
    basics=[x for x in dates if x[3].lower()=="basic"] or dates
    return basics[0][0],basics[0][1],basics[0][2]

NOTE_KEYS=("comment","customer_comment","delivery_comment","recipient_comment","comment_to_delivery","order_comment","note","notes")
def note(o):
    c=o.get("customer") if isinstance(o.get("customer"),dict) else {}
    a=c.get("address") if isinstance(c.get("address"),dict) else {}
    for scope in (o,c,a):
        for k in NOTE_KEYS:
            v=scope.get(k)
            if isinstance(v,str) and v.strip(): return v.strip()
    return ""

def address(o):
    c=o.get("customer") if isinstance(o.get("customer"),dict) else {}
    a=c.get("address") if isinstance(c.get("address"),dict) else {}
    vals=[]
    for k in ("zip_code","country","region","city","address_tail"):
        v=s(a.get(k))
        if v and v not in vals: vals.append(v)
    return ", ".join(vals)

def add(p,t,v):
    e=ET.SubElement(p,t); e.text=s(v); return e

def main():
    o=ozon_order()
    prr=o.get("prr_option") if isinstance(o.get("prr_option"),dict) else {}
    code=s(prr.get("code")).lower()
    price=float(str(prr.get("price") or "0").replace(",","."))
    floor=int(float(str(prr.get("floor") or "0").replace(",",".")))
    if not (price>0 and code in {"lift","stairs"}):
        raise RuntimeError(f"Нет отдельной оплаченной услуги подъёма: code={code}, price={price}")
    climb_type="elevator" if code=="lift" else "stairs"

    current=getbasket()
    cur=current.find(".//order")
    if cur is None: raise RuntimeError("Заказ не найден в корзине Dalli")
    barcode=s(cur.findtext("barcode"))
    c=o.get("customer") if isinstance(o.get("customer"),dict) else {}
    ad=o.get("addressee") if isinstance(o.get("addressee"),dict) else {}
    person=s(ad.get("name") or c.get("name"))
    phone=s(ad.get("phone") or c.get("phone"))
    pin=s(ad.get("pin"))
    if pin: phone += f" доб. {pin}"

    full_addr=address(o)
    delivery_date,time_min,time_max=get_interval(full_addr,"30")

    root=ET.Element("editbasket")
    n=ET.SubElement(root,"order",{"number":ORDER})
    add(n,"barcode",barcode)
    recv=ET.SubElement(n,"receiver")
    add(recv,"address",full_addr); add(recv,"person",person); add(recv,"phone",phone)
    add(recv,"date",delivery_date)
    add(recv,"time_min",time_min)
    add(recv,"time_max",time_max)
    add(n,"service","30")
    if s(cur.findtext("weight")): add(n,"weight",cur.findtext("weight"))
    add(n,"quantity",cur.findtext("quantity") or "1")
    add(n,"paytype",cur.findtext("paytype") or "NO")
    if s(cur.findtext("priced")): add(n,"priced",cur.findtext("priced"))
    add(n,"price",cur.findtext("price") or "0")
    add(n,"inshprice",cur.findtext("inshprice") or "0")
    add(n,"instruction",note(o))
    ads=ET.SubElement(n,"ads")
    ET.SubElement(ads,"climb",{"type":climb_type,"floor":str(max(1,floor))})

    items=ET.SubElement(n,"items")
    for p in [x for x in (o.get("products") or []) if isinstance(x,dict)]:
        qty=max(1,int(p.get("quantity") or 1)); val=float(p.get("price") or 0)
        it=ET.SubElement(items,"item",{
            "quantity":str(qty),"retprice":f"{val:.2f}","inshprice":f"{val:.2f}",
            "article":s(p.get("offer_id"))[:100],"VATrate":"0"})
        it.text=s(p.get("name"))[:250] or s(p.get("offer_id")) or "Товар Ozon"

    resp=dalli(root)
    errs=[{"code":s(e.get("errorCode")),"field":s(e.get("error")),"message":s(e.get("errorMessage"))} for e in resp.findall(".//error")]
    if errs: raise RuntimeError("Dalli editbasket: "+"; ".join(x["message"] for x in errs))

    v=getbasket().find(".//order")
    if v is None: raise RuntimeError("После editbasket заказ не найден")
    cl=v.find("./ads/climb")
    result={
        "ok":True,"order_number":ORDER,"account":"МСК","in_basket":True,"sent_to_delivery":False,
        "barcode":s(v.findtext("barcode")) or barcode,
        "source_prr_code":code,"source_prr_price":price,"source_prr_floor":floor,
        "dalli_climb_present":cl is not None,
        "dalli_climb_type":s(cl.get("type")) if cl is not None else "",
        "dalli_service":s(v.findtext("service")),
        "dalli_delivery_date":s(v.findtext("./receiver/date")),
        "dalli_time_min":s(v.findtext("./receiver/time_min")),
        "dalli_time_max":s(v.findtext("./receiver/time_max")),
        "dalli_climb_floor":s(cl.get("floor")) if cl is not None else "",
        "vat_values":[s(x.get("VATrate")) for x in v.findall("./items/item")],
        "errors":[]
    }
    os.makedirs(os.path.dirname(OUT),exist_ok=True)
    with open(OUT,"w",encoding="utf-8") as f: json.dump(result,f,ensure_ascii=False,indent=2); f.write("\n")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not result["dalli_climb_present"]: raise SystemExit(2)

if __name__=="__main__":
    try: main()
    except Exception as e:
        res={"ok":False,"order_number":ORDER,"account":"МСК","in_basket":True,"sent_to_delivery":False,"error":f"{type(e).__name__}: {e}"}
        os.makedirs(os.path.dirname(OUT),exist_ok=True)
        with open(OUT,"w",encoding="utf-8") as f: json.dump(res,f,ensure_ascii=False,indent=2); f.write("\n")
        print(json.dumps(res,ensure_ascii=False,indent=2))
        raise
