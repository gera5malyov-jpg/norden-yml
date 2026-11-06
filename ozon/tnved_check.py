#!/usr/bin/env python3
"""Read-only verification of the most recent Ozon TN VED bulk update."""
import json
from collections import Counter
from tnved_sync import post, list_products, get_product_attributes

TASK_IDS = [5981953233, 5981953269]

for task_id in TASK_IDS:
    try:
        data = post("/v1/product/import/info", {"task_id": task_id})
        result = data.get("result") or {}
        items = result.get("items") or []
        counts = Counter(str(x.get("status")) for x in items)
        errors = [{"offer_id": x.get("offer_id"), "errors": x.get("errors")} for x in items if x.get("errors")]
        print("TASK", task_id, "statuses", dict(counts), "errors", len(errors), json.dumps(errors[:5], ensure_ascii=False))
    except Exception as e:
        print("TASK", task_id, "check failed", str(e)[:300])

catalog = list_products()
ids = [int(x.get("product_id") or 0) for x in catalog if x.get("product_id")]
products = get_product_attributes(ids)
missing = []
filled = 0
for x in products:
    t = [a for a in x.get("attributes") or [] if int(a.get("id") or 0) == 22232]
    v = [v for a in t for v in a.get("values") or [] if v.get("value") or v.get("dictionary_value_id")]
    if v:
        filled += 1
    else:
        missing.append((x.get("offer_id"), x.get("name")))
print("READBACK products", len(products), "filled", filled, "missing", len(missing))
print("MISSING FIRST 20", json.dumps(missing[:20], ensure_ascii=False))
