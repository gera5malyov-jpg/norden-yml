#!/usr/bin/env python3
import json, os, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path

BASE="https://api.dalli-service.com/v1/"
TOKEN=os.environ["DALLI_TOKEN_MSK"].strip()
ORDER="97426764-0177-1"
OUT=Path("dalli/msk_vat_api_evidence.json")

def post(root):
    root.insert(0, ET.Element("auth", {"token": TOKEN}))
    data=ET.tostring(root,encoding="utf-8",xml_declaration=True)
    req=urllib.request.Request(BASE,data=data,method="POST",headers={"Content-Type":"application/xml; charset=utf-8","Accept":"application/xml, text/xml, */*"})
    with urllib.request.urlopen(req,timeout=60) as r:
        raw=r.read()
    return raw, ET.fromstring(raw)

# Get address + package only to reproduce exact calculator request for one real order.
s=ET.Element("statusreq"); ET.SubElement(s,"orderno").text=ORDER
_, sx=post(s)
o=sx.find("order")
recv=o.find("receiver")
address=((recv.findtext("address","") if recv is not None else "") or "").strip()
town=((recv.findtext("town","") if recv is not None else "") or "").strip()
if town and town.lower() not in address.lower(): address=f"{town}, {address}"
pkgs=[]
for p in o.findall("./packages/package"):
    pkgs.append({
      "weight":p.get("mass",""), "length":p.get("length",""),
      "width":p.get("width",""), "height":p.get("height","")
    })

r=ET.Element("deliverycost")
ET.SubElement(r,"partner").text="DS"
ET.SubElement(r,"to").text=address
ET.SubElement(r,"price").text="0"
ET.SubElement(r,"inshprice").text="0"
ET.SubElement(r,"cashservices").text="NO"
ET.SubElement(r,"withouttax").text="NO"
ps=ET.SubElement(r,"packages")
for p in pkgs:
    ET.SubElement(ps,"package",p)
ET.SubElement(r,"output").text="x2"
raw, x=post(r)

prices=[]
for n in x.findall("price"):
    prices.append(dict(n.attrib))
evidence={
  "source":"official Dalli API",
  "endpoint":BASE,
  "method":"deliverycost",
  "order_reference":ORDER,
  "request_withouttax":"NO",
  "packages":pkgs,
  "response_root":dict(x.attrib),
  "price_nodes":prices,
  "vat_messages":[p.get("msg","") for p in prices if "НДС" in p.get("msg","")]
}
OUT.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(evidence,ensure_ascii=False,indent=2))
