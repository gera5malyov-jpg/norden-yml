#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, json, os, sys, time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"catalog"/"repair_corfu_all_images_report.json"

def load(path,name):
    p=ROOT/path
    spec=importlib.util.spec_from_file_location(name,p)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

# env needed by sync module import
os.environ.setdefault("CATALOG_SPREADSHEET_ID","1DvMYvKRr8fX8mfLFaeJCXCccQofPViPcIP2CgIctc8w")
os.environ.setdefault("GOOGLE_SERVICE_ACCOUNT_JSON","{}")
SYNC=load(Path("catalog")/"sync_norden_chairs_stools_6h.py","sync_repair_corfu")
MOD=SYNC.MOD
BRIDGE=SYNC.BRIDGE
sys.path.insert(0,str(ROOT/"webasyst"))
from client import WebasystClient

YML="B1816 3S fabric LE8100-07"
VID="01a0df2f-3030-7957-a8f4-1561c0d7ca95"
WA_PID="1483282"

src,_=MOD.source_from_xml(short=False)
item=src[YML]
urls=list(dict.fromkeys(item.get("images") or []))

kit=BRIDGE.KitClient()
report={"yml_id":YML,"source_image_count":len(urls),"source_urls":urls,"uploads":[]}
media=[]
for idx,u in enumerate(urls):
    try:
        up=kit.upload_image_url(u)
        fid=str(up.get("id") or "").strip()
        if not fid:
            raise RuntimeError(f"upload returned no id: {up}")
        media.append({"type":"IMAGE","display_sequence":idx,"image_id":fid})
        report["uploads"].append({"url":u,"ok":True,"image_id":fid})
    except Exception as exc:
        report["uploads"].append({"url":u,"ok":False,"error":str(exc)[:1000]})

if len(media)!=len(urls):
    report["ok"]=False
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    raise SystemExit(2)

kit.patch_variant(VID,{"media":media})
full=kit.request("GET",f"/v1/variants/{VID}")
actual=[m for m in (full.get("media") or []) if isinstance(m,dict) and str(m.get("type") or "").upper()=="IMAGE"]
actual.sort(key=lambda m:int(m.get("display_sequence") or 0))
report["kit_image_count"]=len(actual)
report["kit_media"]=actual

kit_urls=SYNC.kit_public_image_urls(kit,VID)
report["kit_public_urls"]=kit_urls

wa=WebasystClient(min_request_interval=0.45)
summary=SYNC.wa_replace_summary_with_kit_images(wa,WA_PID,kit_urls)
report["webasyst_summary"]=summary
report["ok"]=len(actual)==len(urls)==len(kit_urls)
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(report,ensure_ascii=False,indent=2))
raise SystemExit(0 if report["ok"] else 2)
