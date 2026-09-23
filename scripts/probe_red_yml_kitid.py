#!/usr/bin/env python3
import json, os, xml.etree.ElementTree as ET, requests, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deephouse-kit"))
from sync_deephouse_kit import KitClient, s

YML="https://yastore-prod-persist.s3.yandex.net/feeds/yml/019a5a60-ce41-7872-aa9d-d7720c268dab.xml"
kit=KitClient(os.environ["YANDEX_KIT_TOKEN"])
rows=kit.list_all("/v1/variants",{"name":"RED-"},"variants")
red=[v for v in rows if s(v.get("sku")).upper().startswith("RED-")]

r=requests.get(YML,timeout=240,headers={"User-Agent":"Mozilla/5.0 RED-YML-Probe"})
r.raise_for_status()
root=ET.fromstring(r.content)
by_id={}
for e in root.iter():
    if str(e.tag).rsplit("}",1)[-1]!="offer": continue
    oid=s(e.attrib.get("id"))
    pics=[]
    for ch in list(e):
        if str(ch.tag).rsplit("}",1)[-1]=="picture" and s(ch.text):
            pics.append(s(ch.text))
    if oid and pics:
        by_id[oid]=list(dict.fromkeys(pics))

matched=[]
missing=[]
for v in red:
    kid=s(v.get("kit_id"))
    pics=by_id.get(kid,[])
    if pics: matched.append((s(v.get("sku")),kid,len(pics)))
    else: missing.append((s(v.get("sku")),kid))

print(json.dumps({
 "red_variants":len(red),
 "yml_offers_with_images":len(by_id),
 "matched_by_kit_id":len(matched),
 "missing_by_kit_id":len(missing),
 "matched_images":sum(x[2] for x in matched),
 "sample_matched":matched[:10],
 "sample_missing":missing[:30]
},ensure_ascii=False,indent=2))
