#!/usr/bin/env python3
"""Find marking-management SKUs missing from ALL: archived or absent? Read-only."""
import json
from tnved_sync import post,attr_value_tuple,find_product_attr
missing="""AF-31201246 9710020020703 SAMS-290341 SAMS-290313 SAMS-630050 SAMS-631418 SAMS-631430 SAMS-631426 SAMS-631427 SAMS-631425 SAMS-631417 SAMS-631429 SAMS-631431 SAMS-631428 SAMS-631424 SAMS-630049 SAMS-607676 SAMS-630052 SAMS-608476 SAMS-630053 SAMS-607674 SAMS-607675 SAMS-631432 SAMS-291349 SAMS-631419 SAMS-631433 SAMS-631422""".split()
for v in ("ARCHIVED","ALL"):
 try:
  data=post("/v3/product/list",{"filter":{"offer_id":missing,"visibility":v},"limit":1000})
  items=(data.get("result") or {}).get("items") or []
  print("MARKING_ARCHIVE_LIST",json.dumps({"visibility":v,"total":(data.get("result") or {}).get("total"),"items":[{"offer":r.get("offer_id"),"id":r.get("product_id"),"archived":r.get("archived")} for r in items]},ensure_ascii=False),flush=True)
  if v!="ARCHIVED" or not items:continue
  ids=[int(r["product_id"]) for r in items if r.get("product_id")]
  for av in ("ARCHIVED","ALL"):
   try:
    result=post("/v4/product/info/attributes",{"filter":{"product_id":ids,"visibility":av},"limit":1000}).get("result") or []
    print("MARKING_ARCHIVE_ATTRIBUTES",json.dumps({"visibility":av,"count":len(result),"items":[{"offer":p.get("offer_id"),"name":p.get("name"),"id":p.get("id"),"category":p.get("description_category_id"),"type":p.get("type_id"),"tnved":attr_value_tuple(find_product_attr(p,22232) or {}),"mark":attr_value_tuple(find_product_attr(p,23536) or {})} for p in result]},ensure_ascii=False),flush=True)
   except Exception as e:print("MARKING_ARCHIVE_ATTR_ERROR",av,str(e),flush=True)
 except Exception as e:print("MARKING_ARCHIVE_LIST_ERROR",v,str(e),flush=True)
