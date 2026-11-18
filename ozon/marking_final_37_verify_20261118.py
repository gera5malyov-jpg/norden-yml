#!/usr/bin/env python3
"""Independent read-only final Ozon 37-SKU marking page cross-check."""
import json
from pathlib import Path
from tnved_sync import post,find_product_attr,attr_value_tuple
OFFERS=["9710020021834","AF-31201246","9710020020703","SAMS-291372","SAMS-291371","SAMS-290341","SAMS-290313","SAMS-290468","SAMS-290699","SAMS-630050","SAMS-631418","SAMS-631430","SAMS-631426","SAMS-631427","SAMS-631425","SAMS-631417","SAMS-631429","SAMS-631431","SAMS-631428","SAMS-631424","SAMS-630049","SAMS-607676","SAMS-630052","SAMS-608476","SAMS-630053","SAMS-607674","SAMS-607675","SAMS-631432","SAMS-237926","SAMS-231089","SAMS-235604","SAMS-291349","SAMS-603986","SAMS-235514","SAMS-631419","SAMS-631433","SAMS-631422"]
OUT=Path("ozon/marking_final_37_20261118.json")
list_res=post("/v3/product/list",{"filter":{"offer_id":OFFERS,"visibility":"ALL"},"limit":1000})
products={str(x.get("offer_id") or ""):x for x in (list_res.get("result") or {}).get("items") or []}
if len(products)!=len(OFFERS):raise RuntimeError("Catalog incomplete, cannot verify")
ids=[int(x["product_id"]) for x in products.values()]
d=post("/v4/product/info/attributes",{"filter":{"product_id":ids,"visibility":"ALL"},"limit":1000})
attrs={str(x.get("offer_id") or ""):x for x in d.get("result") or []}
if len(attrs)!=len(OFFERS):raise RuntimeError("Product attrs incomplete")
rows=[]
for offer in OFFERS:
 p=attrs[offer];a=attr_value_tuple(find_product_attr(p,22232) or {})
 rows.append({"offer":offer,"code":str(a[1]).split(" - ")[0].strip() if a else None,"code_id":int(a[0]) if a else None,"name":p.get("name"),"archived":bool(products[offer].get("archived")),"marking":attr_value_tuple(find_product_attr(p,23536) or {})})
result={"total":len(rows),"filled":sum(1 for x in rows if x["code"]),"missing":[x for x in rows if not x["code"]],"archived":sum(1 for x in rows if x["archived"]),"active":sum(1 for x in rows if not x["archived"]),"rows":rows}
print("MARKING_FINAL_37",json.dumps({k:v for k,v in result.items() if k!="rows"},ensure_ascii=False),flush=True)
for x in rows:print("MARKING_FINAL_ROW",json.dumps(x,ensure_ascii=False),flush=True)
OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
