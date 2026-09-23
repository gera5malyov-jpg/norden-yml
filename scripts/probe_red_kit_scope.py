#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deephouse-kit"))
from sync_deephouse_kit import KitClient, s

kit = KitClient(os.environ["YANDEX_KIT_TOKEN"])
rows = kit.list_all("/v1/variants", {"name": "RED-"}, "variants")
red = [v for v in rows if s(v.get("sku")).upper().startswith("RED-")]
media = []
for v in red:
    imgs = [m for m in (v.get("media") or []) if isinstance(m, dict) and s(m.get("type")).upper() == "IMAGE"]
    media.append(len(imgs))
print(json.dumps({
    "query_rows": len(rows),
    "red_variants": len(red),
    "with_images": sum(1 for n in media if n),
    "without_images": sum(1 for n in media if not n),
    "total_images": sum(media),
    "max_images": max(media) if media else 0,
    "avg_images": (sum(media)/len(media)) if media else 0,
}, ensure_ascii=False, indent=2))

print("\nFILES_PROBE")
for params in ({"page":1,"per_page":3}, {"page":1,"per_page":100}):
    try:
        payload = kit.request("GET", "/v1/files", params=params)
        print(json.dumps({
            "params": params,
            "keys": list(payload.keys()) if isinstance(payload, dict) else None,
            "total_count": payload.get("total_count") if isinstance(payload, dict) else None,
            "total": payload.get("total") if isinstance(payload, dict) else None,
            "sample": (payload.get("files") or [])[:3] if isinstance(payload, dict) else None,
        }, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({"params":params,"error":str(exc)},ensure_ascii=False))

print("\nPUBLIC_KIT_YML_PROBE")
try:
    import requests
    import xml.etree.ElementTree as ET
    yml_url="https://yastore-prod-persist.s3.yandex.net/feeds/yml/019a5a60-ce41-7872-aa9d-d7720c268dab.xml"
    tmp=Path("/tmp/kit_public_red_probe.xml")
    with requests.get(yml_url,stream=True,timeout=(20,300),headers={"User-Agent":"Megapolis-RED-probe/1.0"}) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(1024*1024):
                if chunk: fh.write(chunk)
    red_offers=0
    red_pictures=0
    red_without=0
    samples=[]
    for _,elem in ET.iterparse(tmp,events=("end",)):
        if elem.tag.split("}")[-1]!="offer":
            continue
        sku=""
        pics=[]
        for child in list(elem):
            tag=child.tag.split("}")[-1]
            val=(child.text or "").strip()
            if tag in ("vendorCode","sku") and val and not sku:
                sku=val
            elif tag=="picture" and val:
                pics.append(val)
        if sku.upper().startswith("RED-"):
            red_offers+=1
            red_pictures+=len(pics)
            if not pics: red_without+=1
            if len(samples)<3: samples.append({"sku":sku,"pictures":pics[:3]})
        elem.clear()
    print(json.dumps({"red_offers":red_offers,"red_pictures":red_pictures,"red_without":red_without,"samples":samples},ensure_ascii=False,indent=2))
except Exception as exc:
    print(json.dumps({"yml_error":str(exc)},ensure_ascii=False))
