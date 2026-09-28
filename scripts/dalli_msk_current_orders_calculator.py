#!/usr/bin/env python3
import json, os, urllib.request, urllib.error, xml.etree.ElementTree as ET
from pathlib import Path

BASE="https://api.dalli-service.com/v1/"
TOKEN=os.environ["DALLI_TOKEN_MSK"].strip()
ORDERS=["97426764-0177-1","80399471-0026-1","67081442-0604-1","37307681-0157-1","61913429313","61653679298"]
OUT=Path("dalli/msk_current_orders_calculator.json")

def post(root):
    root.insert(0,ET.Element("auth",{"token":TOKEN}))
    data=ET.tostring(root,encoding="utf-8",xml_declaration=True)
    req=urllib.request.Request(BASE,data=data,method="POST",headers={"Content-Type":"application/xml; charset=utf-8","Accept":"application/xml"})
    try:
        with urllib.request.urlopen(req,timeout=60) as r: raw=r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:500]}")
    x=ET.fromstring(raw)
    return x

def status(orderno):
    r=ET.Element("statusreq"); ET.SubElement(r,"orderno").text=orderno
    x=post(r); o=x.find("order")
    if o is None: raise RuntimeError("order not found")
    recv=o.find("receiver")
    addr=((recv.findtext("address","") if recv is not None else "") or "").strip()
    town=((recv.findtext("town","") if recv is not None else "") or "").strip()
    if town and town.lower() not in addr.lower(): addr=f"{town}, {addr}"
    pkgs=[]
    for p in o.findall("./packages/package"):
        def num(k):
            try:return float(p.get(k,"0") or 0)
            except:return 0.0
        pkgs.append({"weight_kg":num("mass") or num("weight"),"length_cm":num("length"),"width_cm":num("width"),"height_cm":num("height")})
    dp=o.find("deliveryprice")
    return {
      "orderno":orderno,"address":addr,
      "weight_kg":float(o.findtext("weight","0") or 0),
      "quantity":int(float(o.findtext("quantity","0") or 0)),
      "service":(o.findtext("service","") or "").strip(),
      "actual_deliveryprice_total":float(dp.get("total","0") or 0) if dp is not None else None,
      "packages":pkgs,
    }

def calc(address,packages,total_weight,use_packages=True):
    r=ET.Element("deliverycost")
    ET.SubElement(r,"partner").text="DS"
    ET.SubElement(r,"to").text=address
    ET.SubElement(r,"price").text="0"
    ET.SubElement(r,"inshprice").text="0"
    ET.SubElement(r,"cashservices").text="NO"
    ET.SubElement(r,"withouttax").text="NO"
    if use_packages and packages:
        ps=ET.SubElement(r,"packages")
        for p in packages:
            ET.SubElement(ps,"package",{
              "weight":f'{p["weight_kg"]:g}',"length":f'{p["length_cm"]:g}',
              "width":f'{p["width_cm"]:g}',"height":f'{p["height_cm"]:g}'})
    else:
        ET.SubElement(r,"weight").text=f"{total_weight:g}"
    ET.SubElement(r,"output").text="x2"
    x=post(r)
    if x.get("error"):
        return {"error":x.get("error"),"errormsg":x.get("errormsg",""),"prices":[]}
    prices=[]
    for n in x.findall("price"):
        try: price=float(n.get("price","0"))
        except: price=None
        prices.append({"service":n.get("service",""),"typedelivery":n.get("typedelivery",""),"price":price,"delivery_period":n.get("delivery_period",""),"msg":n.get("msg","")})
    if not prices and x.get("price") is not None:
        try: price=float(x.get("price"))
        except: price=None
        prices=[{"service":x.get("service",""),"typedelivery":x.get("typedelivery",""),"price":price,"delivery_period":x.get("delivery_period",""),"msg":x.get("msg","")}]
    return {"error":None,"prices":prices}

rows=[]
for no in ORDERS:
    s=status(no)
    c=calc(s["address"],s["packages"],s["weight_kg"],True)
    mode="packages_exact"
    if c.get("error"):
        c=calc(s["address"],[],s["weight_kg"],False)
        mode="weight_only_fallback"
    same=[p for p in c.get("prices",[]) if p.get("service")==s["service"]]
    selected=same[0] if same else (next((p for p in c.get("prices",[]) if p.get("typedelivery")=="KUR"),None))
    rows.append({
      "orderno":s["orderno"],"weight_kg":s["weight_kg"],"quantity":s["quantity"],"service":s["service"],
      "packages":s["packages"],"calculation_mode":mode,
      "calculator_price":selected.get("price") if selected else None,
      "calculator_service":selected.get("service") if selected else None,
      "service_match":bool(selected and selected.get("service")==s["service"]),
      "actual_deliveryprice_total":s["actual_deliveryprice_total"],
      "all_prices":c.get("prices",[]),"calculator_error":c.get("errormsg") if c.get("error") else None
    })

OUT.write_text(json.dumps({"account":"MSK","orders":rows},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print("calculated",len(rows),"orders")
