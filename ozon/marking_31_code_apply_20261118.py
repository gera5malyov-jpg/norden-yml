#!/usr/bin/env python3
"""Set only verified TN VED on selected archived/live Ozon marking management SKUs.

DO NOT change marking flags, GTIN, product prices or archive status.
Preserve old values. Only blank TN VED attributes can be updated.
"""
import json,time
from collections import Counter
from pathlib import Path
from tnved_sync import post,get_dictionary_values,find_product_attr,attr_value_tuple
PLAN={
  "SAMS-630050": "3006500000",
  "SAMS-631425": "3006500000",
  "SAMS-631431": "3006500000",
  "SAMS-631418": "3006500000",
  "SAMS-607676": "3006500000",
  "SAMS-631417": "3006500000",
  "SAMS-630049": "3006500000",
  "SAMS-631430": "3006500000",
  "SAMS-631427": "3006500000",
  "SAMS-631428": "3006500000",
  "SAMS-631429": "3006500000",
  "SAMS-631426": "3006500000",
  "SAMS-630052": "3006500000",
  "SAMS-630053": "3006500000",
  "SAMS-607674": "3006500000",
  "SAMS-607675": "3006500000",
  "SAMS-631432": "3006500000",
  "SAMS-631419": "3006500000",
  "SAMS-631433": "3006500000",
  "SAMS-631422": "3006500000",
  "AF-31201246": "9403601009",
  "9710020020703": "9401710009",
  "SAMS-290341": "9403208009",
  "SAMS-290313": "9403208009",
  "SAMS-608476": "3926909709",
  "SAMS-291349": "8303009000",
  "SAMS-237926": "7326909807",
  "SAMS-231089": "7326909807",
  "SAMS-235604": "7326909807",
  "SAMS-603986": "7323990000",
  "SAMS-235514": "7326909807"
}
OUT=Path("ozon/marking_code_batch_report_20261118.json")
report={"status":"STARTED","planned":len(PLAN),"skip_existing":[],"write_candidates":[],"write_submitted":[],"write_errors":[],"readback_success":[],"readback_missing":[],"archive_changed":[]}
def list_exact(offers):
 d=post("/v3/product/list",{"filter":{"offer_id":offers,"visibility":"ALL"},"limit":1000})
 return {str(x.get("offer_id") or ""):x for x in (d.get("result") or {}).get("items") or []}
def get_exact(ids):
 d=post("/v4/product/info/attributes",{"filter":{"product_id":ids,"visibility":"ALL"},"limit":1000})
 return {str(x.get("offer_id") or ""):x for x in d.get("result") or []}
try:
 offers=list(PLAN)
 listed=list_exact(offers)
 if len(listed)!=len(PLAN):
  raise RuntimeError("Some marking SKUs missing: "+str(set(offers)-set(listed)))
 ids=[int(x["product_id"]) for x in listed.values()]
 products=get_exact(ids)
 if len(products)!=len(PLAN):
  raise RuntimeError("Product attributes missing: "+str(set(offers)-set(products)))
 dict_cache={}
 for offer,code in PLAN.items():
  p=products[offer]
  product=listed[offer]
  prior=attr_value_tuple(find_product_attr(p,22232) or {})
  if prior:
   report["skip_existing"].append({"offer":offer,"current":prior})
   if not str(prior[1]).startswith(code):raise RuntimeError("Conflicting existing code for "+offer)
   continue
  group=(int(p.get("description_category_id") or 0),int(p.get("type_id") or 0))
  if group not in dict_cache:
   dict_cache[group]=get_dictionary_values(*group,22232)
  matches=[x for x in dict_cache[group] if str(x.get("value") or "").split(" - ")[0].strip()==code]
  if len(matches)!=1:raise RuntimeError("Code "+code+" not uniquely allowed for category "+str(group)+" offer "+offer)
  report["write_candidates"].append({"offer":offer,"code":code,"value_id":int(matches[0]["id"]),"value":str(matches[0]["value"]),"archived_before":bool(product.get("archived"))})
 if len(report["write_candidates"])>35:
  raise RuntimeError("Safety cap exceeded")
 print("MARKING_PLAN_AUDIT",json.dumps({"all":len(PLAN),"write":len(report["write_candidates"]),"existing":len(report["skip_existing"]),"by_code":dict(Counter(x["code"] for x in report["write_candidates"]))},ensure_ascii=False),flush=True)
 for start in range(0,len(report["write_candidates"]),12):
  batch=report["write_candidates"][start:start+12]
  payload={"items":[{"offer_id":x["offer"],"attributes":[{"complex_id":0,"id":22232,"values":[{"dictionary_value_id":x["value_id"],"value":x["value"]}]}]} for x in batch]}
  d=post("/v1/product/attributes/update",payload)
  task=d.get("task_id")
  if not task:raise RuntimeError("Ozon did not accept update task "+str(d))
  report["write_submitted"].append({"task_id":task,"offers":[x["offer"] for x in batch]})
  print("MARKING_UPDATE_SUBMITTED",task,len(batch),flush=True)
  time.sleep(1)
 for attempt in range(7):
  time.sleep(6)
  fresh=get_exact(ids)
  passed=[]
  missing=[]
  for x in report["write_candidates"]:
   a=fresh.get(x["offer"])
   val=attr_value_tuple(find_product_attr(a,22232) or {}) if a else None
   if val and val[0]==x["value_id"]:passed.append(x["offer"])
   else:missing.append(x["offer"])
  report["readback_success"]=passed
  report["readback_missing"]=missing
  print("MARKING_READBACK",attempt,"confirmed",len(passed),"missing",len(missing),flush=True)
  if not missing:break
 after_list=list_exact(offers)
 for x in report["write_candidates"]:
  if bool(after_list[x["offer"]].get("archived"))!=x["archived_before"]:
   report["archive_changed"].append(x["offer"])
 if report["archive_changed"]:raise RuntimeError("Archive state unexpectedly changed")
 for x in report["write_submitted"]:
  try:
   d=post("/v1/product/import/info",{"task_id":x["task_id"]})
   items=(d.get("result") or {}).get("items") or []
   errs=[{"offer":i.get("offer_id"),"errors":i.get("errors")} for i in items if i.get("errors")]
   print("MARKING_TASK_ERRORS",x["task_id"],json.dumps(errs[:10],ensure_ascii=False),flush=True)
   report["write_errors"].extend(errs)
  except Exception as e:print("TASK_READ_WARNING",x["task_id"],str(e)[:350],flush=True)
 report["status"]="COMPLETE" if not report["readback_missing"] else "PARTIAL"
 print("MARKING_FINAL_REPORT",json.dumps({"status":report["status"],"planned":len(PLAN),"success":len(report["readback_success"]),"existing":len(report["skip_existing"]),"remaining":report["readback_missing"],"archive_changed":report["archive_changed"],"task_errors":len(report["write_errors"])},ensure_ascii=False),flush=True)
finally:
 OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
