#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deephouse-kit"))
from sync_deephouse_kit import KitClient

kit = KitClient(os.environ["YANDEX_KIT_TOKEN"])
a="01980d4c-1b4d-7bc7-ab8f-8fd9a99eff66"
b="01980d52-af7f-738f-a8e0-94597f4e8595"
tests=[
    {"page":1,"per_page":1000},
    {"page":1,"per_page":5000},
    {"page":1,"per_page":100,"id":a},
    {"page":1,"per_page":100,"id":a+","+b},
    {"page":1,"per_page":100,"ids":a+","+b},
    {"page":1,"per_page":100,"ids":[a,b]},
]
out=[]
for params in tests:
    try:
        p=kit.request("GET","/v1/files",params=params)
        files=p.get("files") or []
        out.append({
            "params":params,
            "returned":len(files),
            "total_count":p.get("total_count"),
            "ids":[x.get("id") for x in files[:5]],
        })
    except Exception as e:
        out.append({"params":params,"error":str(e)})
print(json.dumps(out,ensure_ascii=False,indent=2))
