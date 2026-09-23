#!/usr/bin/env python3
import json, os, requests, xml.etree.ElementTree as ET
root=ET.Element("services")
root.insert(0,ET.Element("auth",{"token":os.environ["DALLI_TOKEN_MSK"].strip()}))
r=requests.post("https://api.dalli-service.com/v1/",data=ET.tostring(root,encoding="utf-8",xml_declaration=True),headers={"Content-Type":"application/xml; charset=utf-8"},timeout=60)
r.raise_for_status()
x=ET.fromstring(r.content)
rows=[]
for n in x.findall(".//service"):
    rows.append({"code":(n.findtext("code") or "").strip(),"name":(n.findtext("name") or "").strip()})
print(json.dumps(rows,ensure_ascii=False,indent=2))
