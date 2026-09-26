#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, json, os, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"catalog"/"repair_webasyst_summary_from_kit_report.json"

def load(path,name):
    p=ROOT/path
    spec=importlib.util.spec_from_file_location(name,p)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

SYNC=load(Path("catalog")/"sync_norden_chairs_stools_6h.py","norden_summary_repair")
BRIDGE=SYNC.BRIDGE
sys.path.insert(0,str(ROOT/"webasyst"))
from client import WebasystClient

wa=WebasystClient(min_request_interval=0.45)
kit=BRIDGE.KitClient()
pairs=[
    {"article":"AF-31662420","webasyst_product_id":"1483282","kit_variant_id":"01a0df2f-3030-7957-a8f4-1561c0d7ca95"},
    {"article":"AF-31662421","webasyst_product_id":"1483283","kit_variant_id":"01a0df2f-eb73-7572-b95a-a3fef52bec7f"},
]
report={"ok":True,"items":[]}
for p in pairs:
    urls=SYNC.kit_public_image_urls(kit,p["kit_variant_id"])
    summary=SYNC.wa_replace_summary_with_kit_images(wa,p["webasyst_product_id"],urls)
    info=wa.call("shop.product.getInfo",params={"id":p["webasyst_product_id"]})
    report["items"].append({
        **p,
        "kit_image_urls":urls,
        "summary":summary,
        "verified_summary":str(info.get("summary") or ""),
        "verified":str(info.get("summary") or "").replace("\r\n","\n").strip()==summary.replace("\r\n","\n").strip(),
    })
report["ok"]=all(x["verified"] for x in report["items"])
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(report,ensure_ascii=False,indent=2))
raise SystemExit(0 if report["ok"] else 2)
