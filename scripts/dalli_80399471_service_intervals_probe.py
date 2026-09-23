#!/usr/bin/env python3
import json, os, requests, xml.etree.ElementTree as ET
ORDER="80399471-0026-1"
H={"Client-Id":os.environ["OZON_CLIENT_ID"].strip(),"Api-Key":os.environ["OZON_API_KEY"].strip(),"Content-Type":"application/json"}
r=requests.post("https://api-seller.ozon.ru/v3/posting/fbs/get",headers=H,json={"posting_number":ORDER,"with":{"analytics_data":False,"barcodes":False,"financial_data":False,"translit":False}},timeout=60)
r.raise_for_status(); d=r.json(); o=d.get("result") if isinstance(d,dict) and isinstance(d.get("result"),dict) else d
c=o.get("customer") or {}; a=c.get("address") or {}
vals=[]
for k in ("zip_code","country","region","city","address_tail"):
    v=str(a.get(k) or "").strip()
    if v and v not in vals: vals.append(v)
addr=", ".join(vals)
token=os.environ["DALLI_TOKEN_MSK"].strip()
def post(root):
    root.insert(0,ET.Element("auth",{"token":token}))
    rr=requests.post("https://api.dalli-service.com/v1/",data=ET.tostring(root,encoding="utf-8",xml_declaration=True),headers={"Content-Type":"application/xml; charset=utf-8"},timeout=60)
    rr.raise_for_status(); return ET.fromstring(rr.content)
out=[]
for service in ("30","32","31","11"):
    root=ET.Element("intervals")
    ET.SubElement(root,"address").text=addr
    ET.SubElement(root,"service").text=service
    ET.SubElement(root,"strict").text="T"
    ET.SubElement(root,"output").text="dates"
    ET.SubElement(root,"format").text="minutes"
    x=post(root)
    dates=[]
    for dd in x.findall(".//date"):
        date=(dd.get("value") or "").strip()
        for iv in dd.findall("./intervals/interval"):
            dates.append({"date":date,"time_min":(iv.findtext("time_min") or "").strip(),"time_max":(iv.findtext("time_max") or "").strip(),"type":(iv.get("type") or "").strip()})
    out.append({"service":service,"interval_count":len(dates),"first_intervals":dates[:5]})
print(json.dumps(out,ensure_ascii=False,indent=2))
