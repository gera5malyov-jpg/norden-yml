#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, json, os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"catalog"/"migrate_three_ozon_plain_characteristics_report.json"

def load(path,name):
    p=ROOT/path
    spec=importlib.util.spec_from_file_location(name,p)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

POST=load(Path("catalog")/"sync_ozon_readback_to_kit.py","post")
BRIDGE=load(Path("norden-kit")/"webasyst_n100_to_kit_once.py","bridge")

targets={
  "AF-31662372":"01a0dfaa-3c5e-7939-9898-c89cc10b6b53",
  "AF-31662420":"01a0df2f-3030-7957-a8f4-1561c0d7ca95",
  "AF-31662421":"01a0df2f-eb73-7572-b95a-a3fef52bec7f",
}

report={"status":"УСПЕШНО","items":[]}
for offer,vid in targets.items():
    item={"offer_id":offer,"variant_id":vid}
    try:
        item["sync"]=POST.sync_offer_to_kit(offer,vid)
    except Exception as e:
        item["error"]=str(e)[:4000]
        report["status"]="ЗАВЕРШЕНО С ОШИБКАМИ"
    report["items"].append(item)

# Independent readback from KIT: no technical/prefixed Ozon characteristics may remain.
kit=BRIDGE.KitClient()
chars=kit.characteristics()
title_by_id={str(x.get("id") or ""):str(x.get("title") or "") for x in chars}
for item in report["items"]:
    vid=item["variant_id"]
    try:
        v=kit.request("GET",f"/v1/variants/{vid}")
        assigned=[]
        for c in v.get("characteristics") or []:
            title=title_by_id.get(str(c.get("characteristic_id") or ""),"")
            assigned.append({"title":title,"value":str(c.get("value") or "")})
        bad=[x for x in assigned if x["title"].startswith("Ozon ") or any(k in x["title"].casefold() for k in (
          "dictionary_value_id","source_product_id","source_offer_id","source_sku","readback_at",
          "moderate_status","validation_status","description_category_id","type_id"
        ))]
        item["readback"]={
          "total_characteristics":len(assigned),
          "bad_technical_count":len(bad),
          "bad_technical":bad,
          "sample":assigned[:80],
        }
        if bad:
            report["status"]="ЗАВЕРШЕНО С ОШИБКАМИ"
    except Exception as e:
        item["readback_error"]=str(e)[:2000]
        report["status"]="ЗАВЕРШЕНО С ОШИБКАМИ"

OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({
  "status":report["status"],
  "items":[{
    "offer_id":x["offer_id"],
    "sync_status":(x.get("sync") or {}).get("status"),
    "customer_count":(x.get("sync") or {}).get("customer_characteristic_count"),
    "bad_technical_count":(x.get("readback") or {}).get("bad_technical_count")
  } for x in report["items"]]
},ensure_ascii=False,indent=2))
raise SystemExit(0 if report["status"]=="УСПЕШНО" else 2)
