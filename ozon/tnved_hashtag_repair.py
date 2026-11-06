#!/usr/bin/env python3
"""Repair invalid Ozon hashtags blocking TN VED updates on exactly seven offers.

This only writes attributes 23171 (hashtags) and 22232 (TN VED).
Base price, description, names, stock and all other attributes remain unchanged.
"""
import json
from tnved_sync import post, get_dictionary_values, get_category_attributes, is_tnved_name

WEST = [
"RED-00-00012003","RED-00-00010489","RED-00-00012053",
"RED-00-00012051","RED-00-00010541","RED-00-00010505",
]
IZO = "SAMS-530581"
ALL = WEST + [IZO]
CLEAN_WEST="#кресло #креслоДляОтдыха #мягкоеКресло #креслоДляГостиной #креслоДляКабинета #креслоДляЧтения #креслоСПодлокотниками #креслоДляСпальни"
CLEAN_IZO="#стул #офисныйСтул #стулДляПосетителей #стулДляПерсонала #офиснаяМебель #стулСМеталлическимКаркасом"
info = post("/v4/product/info/attributes",{"filter":{"offer_id":ALL,"visibility":"ALL"},"limit":1000})
rows={str(x.get("offer_id")):x for x in info.get("result") or []}
if set(rows)!=set(ALL):
    raise SystemExit("Unexpected/missing offers: "+repr(set(rows)^set(ALL)))
items=[]
for offer in ALL:
    p=rows[offer]
    group=(int(p.get("description_category_id") or 0),int(p.get("type_id") or 0))
    if offer in WEST:
        if group!=(80375615,95030):raise SystemExit("Unexpected West category")
        code="9401610000"; clean=CLEAN_WEST
    else:
        if group!=(17027916,95048):raise SystemExit("Unexpected IZO category")
        code="9401710009";clean=CLEAN_IZO
    attributes={int(a.get("id") or 0):a for a in p.get("attributes") or []}
    old=attributes.get(23171,{}).get("values") or []
    old_text=" ".join(str(x.get("value") or "") for x in old)
    if not old_text or "#кресло" not in old_text and offer in WEST:
        raise SystemExit("Unexpected current West hashtags on "+offer)
    if not old_text or "#стул" not in old_text and offer==IZO:
        raise SystemExit("Unexpected current IZO hashtags on "+offer)
    if attributes.get(22232,{}).get("values"):
        print("ALREADY_HAS_CODE",offer,flush=True)
        continue
    cattrs=get_category_attributes(*group)
    tnved=[x for x in cattrs if is_tnved_name(x.get("name"))]
    if len(tnved)!=1 or int(tnved[0].get("id") or 0)!=22232:
        raise SystemExit("Cannot verify TN VED attribute for "+offer)
    choices=get_dictionary_values(group[0],group[1],22232)
    matching=[x for x in choices if str(x.get("value") or "").split(" - ")[0].strip()==code]
    if len(matching)!=1:raise SystemExit("Unexpected dictionary for "+offer)
    m=matching[0]
    items.append({
      "offer_id":offer,
      "attributes":[
        {"complex_id":0,"id":23171,"values":[{"dictionary_value_id":0,"value":clean}]},
        {"complex_id":0,"id":22232,"values":[{"dictionary_value_id":int(m["id"]),"value":m["value"]}]}
      ]
    })
    print("PREPARED",offer,"TN VED",code,"removed offending old hashtags",flush=True)
if not items:raise SystemExit("No missing TN VED offers to repair")
r=post("/v1/product/attributes/update",{"items":items})
print("HASHTAG_TNVED_WRITE",json.dumps(r,ensure_ascii=False),flush=True)
