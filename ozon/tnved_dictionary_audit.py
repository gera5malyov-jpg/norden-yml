#!/usr/bin/env python3
"""Read-only dictionary lookup for unresolved Ozon TN VED product types."""
import json
import os
from pathlib import Path
from tnved_sync import get_category_attributes, get_dictionary_values, is_tnved_name, post

GROUPS = [
 (17027916,95048),(17027916,95053),(80375615,95030),
 (17027908,93670),(89280910,970585206),(80770870,95009),
 (80375615,95024),(17027916,95023),(44082285,970848956),
 (17028733,91958),(17027917,970713329),(17027918,95002),
 (80711064,971162059),(17027919,971161297),(17029020,94491)
]
PREFIXES=("9401","9402","9403","8303","4602","8716","3924","3926","9610","4911","9608","7326","7616","4823","6307")
def run():
    ret={}
    for group in GROUPS:
        key=f"{group[0]}:{group[1]}"
        try:
            attrs=get_category_attributes(*group)
            attr=[a for a in attrs if is_tnved_name(a.get("name"))]
            if len(attr)!=1:
                ret[key]={"error":"TN VED attribute not unique","attrs":[(a.get("id"),a.get("name")) for a in attrs if 'вэд' in str(a.get("name","")).lower()]}
                continue
            aid=int(attr[0]["id"])
            vals=get_dictionary_values(group[0],group[1],aid)
            selected=[{"id":int(v.get("id") or 0),"value":v.get("value")} for v in vals if str(v.get("value") or "").replace(" ","").startswith(PREFIXES)]
            ret[key]={"attribute_id":aid,"all_count":len(vals),"selected":selected}
            print("GROUP",key,"values",len(vals),"selected",len(selected),flush=True)
        except Exception as e:
            ret[key]={"error":str(e)[:1000]}
            print("ERROR",key,str(e)[:250],flush=True)
    Path("ozon/tnved_dictionary_20261106.json").write_text(json.dumps(ret,ensure_ascii=False,indent=2),encoding="utf-8")
if __name__=="__main__":run()
