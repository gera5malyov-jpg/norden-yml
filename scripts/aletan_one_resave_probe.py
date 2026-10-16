#!/usr/bin/env python3
"""One-product API no-op save probe for Aletan. Never mass updates."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"webasyst"))
from client import WebasystClient

PRODUCT_ID="1476508"  # One Aletan test item with empty yml_id.
CLIENT=WebasystClient(min_request_interval=0.5)

def get():
    p=CLIENT.call("shop.product.getInfo",params={"id":PRODUCT_ID})
    if not isinstance(p,dict) or str(p.get("id")) != PRODUCT_ID or str(p.get("type_id")) != "202":
        raise RuntimeError("Product type/id mismatch: refusing save")
    sku=p.get("skus")
    if isinstance(sku,dict): sku=list(sku.values())
    return {
        "id":p["id"],"name":p.get("name"),"type_id":p.get("type_id"),
        "sku":sku,"price":p.get("price"),"count":p.get("count"),
        "images":len(p.get("images") or []),
        "status":p.get("status"),"yml_id":p.get("yml_id"),
        "yml_profile_id":p.get("yml_profile_id"),
        "yml_updated":p.get("yml_updated"),
        "edit_datetime":p.get("edit_datetime")
    }

before=get()
print("BEFORE",{k:v for k,v in before.items() if k!="sku"},"SKU_CODES",
      [x.get("sku") for x in before["sku"] if isinstance(x,dict)])
if before["yml_id"] not in (None,"") or str(before["yml_profile_id"])!="373":
    raise RuntimeError("This test product was linked already; do not overwrite")
if not before["name"] or before["images"]<1:
    raise RuntimeError("Product incomplete, skip no-op save")
result=CLIENT.call("shop.product.update",http_method="POST",
                   params={"id":PRODUCT_ID},
                   data={"name":str(before["name"])})
print("SAVE_API_RESULT_TYPE",type(result).__name__)
after=get()
print("AFTER",{k:v for k,v in after.items() if k!="sku"},"SKU_CODES",
      [x.get("sku") for x in after["sku"] if isinstance(x,dict)])
for field in ("name","type_id","sku","price","count","images","status"):
    if before[field]!=after[field]:
        print("UNEXPECTED_CHANGE",field, "old/new different")
print("YML_ID_CHANGED",before["yml_id"]!=after["yml_id"])
print("YML_PROFILE_CHANGED",before["yml_profile_id"]!=after["yml_profile_id"])
print("SINGLE_TEST_COMPLETE")
