#!/usr/bin/env python3
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webasyst"))
from client import WebasystClient

TARGETS = [
    "AF-31651503",
    "AF-31651509",
    "AF-31651463",
    "AF-31651880",
    "AF-31652568",
]

wa = WebasystClient(min_request_interval=0.1)
out = {}
for sku in TARGETS:
    entry = {"search": [], "details": []}
    try:
        payload = wa.call(
            "shop.product.search",
            params={"hash": f"search/sku={sku}", "limit": 10, "fields": "id,name,skus"},
        )
        products = payload.get("products") if isinstance(payload, dict) else payload if isinstance(payload, list) else []
        for product in products or []:
            entry["search"].append({
                "id": product.get("id"),
                "name": product.get("name"),
                "skus": product.get("skus"),
            })
            pid = product.get("id")
            if not pid:
                continue
            try:
                detail = wa.call("shop.product.get", params={"id": pid})
                if isinstance(detail, dict):
                    keep = {}
                    for key in (
                        "id", "name", "sku_id", "skus", "features", "params",
                        "category_id", "type_id", "summary", "meta_keywords",
                        "meta_description"
                    ):
                        if key in detail:
                            keep[key] = detail.get(key)
                    entry["details"].append(keep)
                else:
                    entry["details"].append({"raw_type": type(detail).__name__})
            except Exception as exc:
                entry["details"].append({"error": str(exc)})
    except Exception as exc:
        entry["error"] = str(exc)
    out[sku] = entry

print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
