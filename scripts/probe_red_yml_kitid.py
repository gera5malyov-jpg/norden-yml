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
by_code={}
for e in root.iter():
    if str(e.tag).rsplit("}",1)[-1]!="offer": continue
    oid=s(e.attrib.get("id"))
    code=""
    pics=[]
    for ch in list(e):
        tag=str(ch.tag).rsplit("}",1)[-1]
        if tag in {"vendorCode","sku"} and s(ch.text) and not code:
            code=s(ch.text)
        if tag=="picture" and s(ch.text):
            pics.append(s(ch.text))
    pics=list(dict.fromkeys(pics))
    if oid and pics:
        by_id[oid]=pics
    if code and pics:
        by_code[code.casefold().replace(" ","")]=pics

matched_id=[]
matched_code=[]
missing=[]
for v in red:
    sku=s(v.get("sku"))
    kid=s(v.get("kit_id"))
    pics=by_id.get(kid,[])
    if pics:
        matched_id.append((sku,kid,len(pics)))
        continue
    source_code=sku[len("RED-"):] if sku.upper().startswith("RED-") else sku
    pics=by_code.get(source_code.casefold().replace(" ",""),[])
    if pics:
        matched_code.append((sku,source_code,len(pics)))
    else:
        missing.append((sku,kid,source_code))

print(json.dumps({
 "red_variants":len(red),
 "yml_offers_with_images":len(by_id),
 "yml_codes_with_images":len(by_code),
 "matched_by_kit_id":len(matched_id),
 "matched_by_source_code":len(matched_code),
 "missing_after_both":len(missing),
 "matched_images":sum(x[2] for x in matched_id)+sum(x[2] for x in matched_code),
 "sample_code_matches":matched_code[:10],
 "sample_missing":missing[:30]
},ensure_ascii=False,indent=2))
