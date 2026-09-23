#!/usr/bin/env python3
import json, os, xml.etree.ElementTree as ET, requests, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deephouse-kit"))
from sync_deephouse_kit import KitClient, s

YML="https://yastore-prod-persist.s3.yandex.net/feeds/yml/019a5a60-ce41-7872-aa9d-d7720c268dab.xml"
kit=KitClient(os.environ["YANDEX_KIT_TOKEN"])
rows=kit.list_all("/v1/variants",{"name":"RED-"},"variants")
red=[v for v in rows if s(v.get("sku")).upper().startswith("RED-")]
sample=red[0]
target_name=s(sample.get("name"))
target_sku=s(sample.get("sku"))
r=requests.get(YML,timeout=240,headers={"User-Agent":"Mozilla/5.0 RED-YML-Name-Probe"})
r.raise_for_status()
root=ET.fromstring(r.content)
matches=[]
for e in root.iter():
    if str(e.tag).rsplit("}",1)[-1]!="offer": continue
    children={}
    pictures=[]
    for ch in list(e):
        tag=str(ch.tag).rsplit("}",1)[-1]
        val=s(ch.text)
        if tag=="picture" and val: pictures.append(val)
        elif val: children.setdefault(tag,[]).append(val)
    names=children.get("name",[])
    if target_name in names:
        matches.append({
          "attributes":dict(e.attrib),
          "vendorCode":children.get("vendorCode",[]),
          "sku":children.get("sku",[]),
          "url":children.get("url",[]),
          "name":names,
          "pictures":pictures[:5],
          "picture_count":len(pictures),
          "params":children.get("param",[])[:20],
        })
print(json.dumps({
 "target":{"sku":target_sku,"kit_id":sample.get("kit_id"),"id":sample.get("id"),"name":target_name,"relative_link_url":sample.get("relative_link_url")},
 "name_matches":len(matches),
 "matches":matches[:10]
},ensure_ascii=False,indent=2))
