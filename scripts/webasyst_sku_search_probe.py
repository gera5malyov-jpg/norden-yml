#!/usr/bin/env python3
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"webasyst"))
from client import WebasystClient
wa=WebasystClient(min_request_interval=0.1)
out={}
for h in ["search/query=AF-31646769","search/query=334-323459","search/sku=AF-31646769"]:
  try:
    p=wa.call("shop.product.search",params={"hash":h,"limit":10,"fields":"id,name,skus"})
    rows=p.get("products") if isinstance(p,dict) else p if isinstance(p,list) else []
    out[h]={"count":len(rows or []),"codes":[]}
    for prod in rows or []:
      skus=prod.get("skus") or {}
      skus=list(skus.values()) if isinstance(skus,dict) else skus if isinstance(skus,list) else []
      out[h]["codes"].extend([str(x.get("sku") or "") for x in skus if isinstance(x,dict)])
  except Exception as e: out[h]={"error":str(e)}
print(json.dumps(out,ensure_ascii=False,indent=2))
