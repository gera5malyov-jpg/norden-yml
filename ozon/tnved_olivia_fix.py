#!/usr/bin/env python3
"""Fill TN VED on the wooden Olivia four-stool set; inspect mixed dining sets."""
import json
from tnved_sync import post,get_category_attributes,get_dictionary_values,is_tnved_name

OFFER="RED-00-00010594"
all_offers=[OFFER,"RED-00-00013040","RED-00-00013041","RED-00-00013039","9710020021834","AF-31353909"]
resp=post("/v4/product/info/attributes",{"filter":{"offer_id":all_offers,"visibility":"ALL"},"limit":1000})
records={str(p.get("offer_id")):p for p in (resp.get("result") or [])}
for oid in all_offers:
    row=records.get(oid)
    if not row:
        print("NOT_FOUND",oid,flush=True);continue
    attr=[a for a in row.get("attributes") or [] if int(a.get("id") or 0)==22232]
    print("PRODUCT",oid,"name",row.get("name"),"group",row.get("description_category_id"),row.get("type_id"),"tnved",json.dumps(attr,ensure_ascii=False),flush=True)

group=(17027916,95053)
attrs=get_category_attributes(*group)
tnved=[a for a in attrs if is_tnved_name(a.get("name"))]
if len(tnved)!=1 or int(tnved[0]["id"])!=22232:raise SystemExit("TNVED attr mismatch")
if not records.get(OFFER):raise SystemExit("Olivia missing")
p=records[OFFER]
if (int(p.get("description_category_id") or 0),int(p.get("type_id") or 0))!=group:raise SystemExit("Olivia category changed")
if any(int(a.get("id") or 0)==22232 and a.get("values") for a in p.get("attributes") or []):
    print("OLIVIA_ALREADY_FILLED");raise SystemExit(0)
dicts=get_dictionary_values(*group,22232)
matches=[x for x in dicts if str(x.get("value") or "").startswith("9401690000 -")]
if len(matches)!=1:raise SystemExit("Ambiguous Olivia Ozon dictionary")
val=matches[0]
payload={"items":[{"offer_id":OFFER,"attributes":[{"complex_id":0,"id":22232,"values":[{"dictionary_value_id":int(val["id"]),"value":val["value"]}]}]}]}
r=post("/v1/product/attributes/update",payload)
print("OLIVIA_WRITE",json.dumps(r,ensure_ascii=False),flush=True)

mattrs=get_category_attributes(80770870,95009)
for a in mattrs:
    if int(a.get("id") or 0)==22232:
        print("DINING_GROUP_TNVED_META",json.dumps(a,ensure_ascii=False)[:3200],flush=True)
