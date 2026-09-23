#!/usr/bin/env python3
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webasyst"))
from client import WebasystClient

wa = WebasystClient(min_request_interval=0.1)
out = {}
for h in ["search/query=RED-", "search/sku=RED-"]:
    try:
        p = wa.call("shop.product.search", params={"hash": h, "limit": 1000, "fields": "id,name,type_id,skus"})
        rows = p.get("products") if isinstance(p, dict) else p if isinstance(p, list) else []
        skus = []
        for prod in rows or []:
            ss = prod.get("skus") or {}
            ss = list(ss.values()) if isinstance(ss, dict) else ss if isinstance(ss, list) else []
            skus.extend(str(x.get("sku") or "") for x in ss if isinstance(x, dict))
        out[h] = {
            "returned_products": len(rows or []),
            "payload_count": p.get("count") if isinstance(p, dict) else None,
            "payload_total_count": p.get("total_count") if isinstance(p, dict) else None,
            "red_skus": [x for x in skus if x.upper().startswith("RED-")][:20],
            "all_skus_sample": skus[:20],
        }
    except Exception as e:
        out[h] = {"error": str(e)}
print(json.dumps(out, ensure_ascii=False, indent=2))
