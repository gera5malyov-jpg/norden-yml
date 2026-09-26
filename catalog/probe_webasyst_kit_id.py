#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"webasyst"))
from client import WebasystClient
wa=WebasystClient(min_request_interval=0.4)
types=wa.call("shop.type.getList")
rows=types if isinstance(types,list) else list(types.values()) if isinstance(types,dict) else []
n=[x for x in rows if str(x.get("name") or x.get("title") or "").strip().casefold()=="norden-100"]
out={"type_matches":n}
if len(n)==1:
    tid=str(n[0].get("id"))
    out["type_id"]=tid
    out["features_for_type"]=wa.call("shop.feature.getList",params={"type_id":tid})
try:
    out["kit_id_info"]=wa.call("shop.feature.getInfo",params={"code":"kit_id"})
except Exception as e:
    out["kit_id_error"]=str(e)
for pid in ["1483234","1483282","1483283"]:
    try:
        info=wa.call("shop.product.getInfo",params={"id":pid})
        out.setdefault("products",{})[pid]={"name":info.get("name"),"features":info.get("features"),"params":info.get("params"),"yml_id":info.get("yml_id")}
    except Exception as e:
        out.setdefault("products",{})[pid]={"error":str(e)}
Path("catalog/webasyst_kit_id_probe.json").write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
print(json.dumps({"type_id":out.get("type_id"),"kit_id_info":out.get("kit_id_info"),"kit_id_error":out.get("kit_id_error")},ensure_ascii=False,default=str))
