#!/usr/bin/env python3
"""Single archived first-aid kit TN VED write probe. Never unarchives."""
import json,time
from tnved_sync import post,get_dictionary_values,find_product_attr,attr_value_tuple
OFFER="SAMS-631424"; TARGET="3006500000"
data=post("/v3/product/list",{"filter":{"visibility":"ARCHIVED","offer_id":[OFFER]},"limit":100})
items=(data.get("result") or {}).get("items") or []
matches=[x for x in items if x.get("offer_id")==OFFER and x.get("archived") is True]
if len(matches)!=1:raise RuntimeError("Target not present as archived, no writes")
pid=int(matches[0]["product_id"])
def get():
 r=post("/v4/product/info/attributes",{"filter":{"product_id":[pid],"visibility":"ARCHIVED"},"limit":100}).get("result") or []
 if len(r)!=1:raise RuntimeError("Archived product attributes missing")
 return r[0]
p=get()
g=(int(p["description_category_id"]),int(p["type_id"]))
if g!=(52619056,95568):raise RuntimeError("Unexpected Ozon category/type")
prior=attr_value_tuple(find_product_attr(p,22232) or {})
if prior:raise RuntimeError("Already has TN VED: "+repr(prior))
v=get_dictionary_values(*g,22232)
matched=[x for x in v if str(x.get("value") or "").split(" - ")[0].strip()==TARGET]
if len(matched)!=1:raise RuntimeError("Exact code unavailable")
val=matched[0]
payload={"items":[{"offer_id":OFFER,"attributes":[{"complex_id":0,"id":22232,"values":[{"dictionary_value_id":val["id"],"value":val["value"]}]}]}]}
r=post("/v1/product/attributes/update",payload)
print("MARKING_ARCHIVED_PROBE_RESPONSE",json.dumps(r,ensure_ascii=False),flush=True)
task=r.get("task_id")
if not task:raise RuntimeError("Update not accepted")
for i in range(5):
 time.sleep(6)
 after=get()
 updated=attr_value_tuple(find_product_attr(after,22232) or {})
 print("MARKING_ARCHIVED_PROBE_READBACK",i,updated,flush=True)
 if updated and updated[0]==val["id"]:break
else:
 try:
  d=post("/v1/product/import/info",{"task_id":task})
  print("MARKING_ARCHIVED_PROBE_TASK",json.dumps(d,ensure_ascii=False)[:3000],flush=True)
 except Exception as e:print("TASK_CHECK_ERROR",str(e),flush=True)
 raise RuntimeError("Archive TN VED change not visible")
r=post("/v3/product/list",{"filter":{"visibility":"ARCHIVED","offer_id":[OFFER]},"limit":100})
matches=[x for x in (r.get("result") or {}).get("items") or [] if x.get("offer_id")==OFFER and x.get("archived")]
if len(matches)!=1:raise RuntimeError("Product no longer archived: abort further writes")
print("MARKING_ARCHIVED_PROBE_SUCCESS",OFFER,TARGET,"kept_archived",True,flush=True)
