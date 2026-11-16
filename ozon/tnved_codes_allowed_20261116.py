#!/usr/bin/env python3
"""Read-only lookup: allowed 10-digit codes for Nov 16 unresolved Ozon categories."""
import json
from tnved_sync import get_category_attributes, get_dictionary_values, is_tnved_name
groups=[
(17027919,971161297),(17027919,95108),(93167672,98058),(17029021,971747556),
(17027919,93671),(80731485,95098),(17028714,970855518),(17028714,93697),
(17029020,94491),(44082285,970848956),(17027916,95048),(89280910,970585206),
(17028690,98241),(17028933,91495),(80770870,95009)
]
wanted=[
"9403109300","9403109100","9403105800","9403109801","9403109809","9403208009",
"8303009000","8303004000","8302500000","8304000000","7323990000","7323930000",
"3924900009","3926100000","3926909709","9610000000","4421100000",
"9401790009","9401710009","9401610000","8716800000","9010600000","9403601009"
]
r={}
for category,type_id in groups:
    g=f"{category}:{type_id}"
    try:
        attrs=get_category_attributes(category,type_id)
        attr=[a for a in attrs if is_tnved_name(a.get("name"))]
        if len(attr)!=1: raise ValueError("No unique TN VED attribute")
        vals=get_dictionary_values(category,type_id,int(attr[0]["id"]))
        options={}
        for v in vals:
            c=str(v.get("value") or "").split(" - ")[0].strip()
            if c in wanted:
                options[c]={"id":v.get("id"),"title":str(v.get("value"))[:180]}
        r[g]={"attribute_id":attr[0]["id"],"dictionary_total":len(vals),"options":options}
        print("GROUP",g,"total",len(vals),"allowed",json.dumps(options,ensure_ascii=False),flush=True)
    except Exception as e:
        r[g]={"error":str(e)}
        print("ERROR",g,str(e),flush=True)
with open("ozon/tnved_codes_allowed_20261116.json","w") as f:json.dump(r,f,ensure_ascii=False,indent=2)
