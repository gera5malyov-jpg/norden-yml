#!/usr/bin/env python3
"""Read-only investigation of Ozon attributes skipped by import task."""
import json
from tnved_sync import post
OFFERS=["RED-00-00012003","RED-00-00010489","RED-00-00012053","RED-00-00012051","RED-00-00010541","RED-00-00010505","SAMS-530581"]
for task_id in [5981992617]:
    try:
        r=post("/v1/product/import/info",{"task_id":task_id}).get("result") or {}
        for item in r.get("items") or []:
            if item.get("offer_id") in OFFERS:
                print("IMPORT",json.dumps(item,ensure_ascii=False)[:3500],flush=True)
    except Exception as exc: print("IMPORT_ERROR",str(exc)[:500],flush=True)
try:
    d=post("/v3/product/info/list",{"offer_id":OFFERS})
    for item in d.get("items") or []:
        print("PRODUCT",json.dumps({k:v for k,v in item.items() if k in (
          "offer_id","id","name","status","statuses","visibility_details","errors",
          "description_category_id","type_id","model_info","has_fbo_stocks","has_fbs_stocks",
          "is_archived","is_discounted","is_kgt","last_update_date","moderate_status")},ensure_ascii=False)[:3500],flush=True)
except Exception as exc:print("PRODUCT_ERROR",str(exc)[:1500],flush=True)
try:
    d=post("/v4/product/info/attributes",{"filter":{"offer_id":OFFERS,"visibility":"ALL"},"limit":1000})
    for item in d.get("result") or []:
        print("ATTR",item.get("offer_id"),"id",item.get("id"),
          "tnved_and_hashtags",json.dumps([x for x in item.get("attributes") or [] if int(x.get("id") or 0) in (22232,23536,23171)],ensure_ascii=False),
          "group",item.get("description_category_id"),item.get("type_id"),flush=True)
except Exception as exc:print("ATTR_ERROR",str(exc)[:1500],flush=True)
