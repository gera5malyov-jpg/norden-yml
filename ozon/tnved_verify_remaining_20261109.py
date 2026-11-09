#!/usr/bin/env python3
"""Read-only full Ozon catalog TN VED verification; no product changes."""
import json
from pathlib import Path
from tnved_sync import list_products, get_product_attributes, attr_value_tuple, find_product_attr

catalog=list_products()
ids=[int(x.get("product_id") or 0) for x in catalog if int(x.get("product_id") or 0)]
products=get_product_attributes(ids)
if len(products)!=len(catalog):
    raise SystemExit(f"Incomplete API pagination: catalog={len(catalog)}, attributes={len(products)}")
missing=[]
for p in products:
    attr=find_product_attr(p,22232)
    if not attr_value_tuple(attr or {}):
        missing.append({
            "offer_id":p.get("offer_id"),
            "name":p.get("name"),
            "product_id":p.get("id"),
            "description_category_id":p.get("description_category_id"),
            "type_id":p.get("type_id"),
        })
summary={
    "catalog_total":len(catalog),
    "tnved_filled":len(products)-len(missing),
    "tnved_missing":len(missing),
    "missing":missing,
}
Path("ozon/tnved_final_readback_20261109.json").write_text(
    json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"
)
print("TNVED_FINAL_READBACK",json.dumps(summary,ensure_ascii=False),flush=True)
