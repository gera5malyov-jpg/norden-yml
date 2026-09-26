#!/usr/bin/env python3
import importlib.util, json, os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def load(path,name):
    p=ROOT/path; spec=importlib.util.spec_from_file_location(name,p); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
B=load(Path("norden-kit")/"webasyst_n100_to_kit_once.py","kitprobe")
kit=B.KitClient()
ids=["01a0df2f-3030-7957-a8f4-1561c0d7ca95","01a0df2f-eb73-7572-b95a-a3fef52bec7f"]
out=[]
for vid in ids:
    v=kit.request("GET",f"/v1/variants/{vid}")
    media=v.get("media") or []
    resolved=[]
    for m in media:
        iid=str((m or {}).get("image_id") or "").strip()
        row={"image_id":iid}
        if iid:
            for path in (f"/v1/files/{iid}", f"/v1/images/{iid}"):
                try:
                    data=kit.request("GET",path)
                    row[path]=data
                except Exception as exc:
                    row[path+"_error"]=str(exc)[:500]
        resolved.append(row)
    out.append({"variant_id":vid,"media":media,"resolved":resolved,"raw_keys":sorted(v.keys()) if isinstance(v,dict) else []})
Path("catalog/kit_media_probe.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(out,ensure_ascii=False,indent=2))
