#!/usr/bin/env python3
import importlib.util, json, os
from pathlib import Path

ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("norden_sync", ROOT/"sync_norden_kit.py")
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
kit=mod.KitClient(os.environ.get("YANDEX_KIT_TOKEN",""))
chars=kit.characteristics()
matches=[x for x in chars if mod.norm_title(x.get("title"))==mod.norm_title(mod.CODE_SITE_TITLE)]
code_id=mod.s(matches[0].get("id")) if len(matches)==1 else ""
targets=["AF-30756476","100-1594419"]
out=[]
for target in targets:
    payload=kit.request("GET","/v1/variants",params={"name":target,"page":1,"per_page":100})
    rows=kit.items(payload)
    exact=[row for row in rows if mod.s(row.get("sku"))==target]
    for row in exact:
        out.append({
            "variant_id":mod.s(row.get("id")),
            "kit_id":row.get("kit_id"),
            "sku":mod.s(row.get("sku")),
            "name":mod.s(row.get("name")),
            "brand":mod.s(row.get("brand")),
            "status":mod.s(row.get("status")),
            "code_for_site":mod.current_char_value(row,code_id) if code_id else "",
            "characteristics":row.get("characteristics") or [],
            "stocks":row.get("stocks") or [],
            "pricing":row.get("pricing") or {},
        })
print(json.dumps(out,ensure_ascii=False,indent=2))
