#!/usr/bin/env python3
"""Audit only the 37 currently flagged Ozon marking-management SKUs.

Read-only: compare live product attribute 22232 and marking flag 23536 to
marking-management recommendations, and print category-verified candidates.
"""
import json
from pathlib import Path
from tnved_sync import list_products,get_product_attributes,get_category_attributes,get_dictionary_values,is_tnved_name,attr_value_tuple,find_product_attr
OFFER_IDS="""9710020021834 AF-31201246 9710020020703 SAMS-291372 SAMS-291371 SAMS-290341 SAMS-290313 SAMS-290468 SAMS-290699 SAMS-630050 SAMS-631418 SAMS-631430 SAMS-631426 SAMS-631427 SAMS-631425 SAMS-631417 SAMS-631429 SAMS-631431 SAMS-631428 SAMS-631424 SAMS-630049 SAMS-607676 SAMS-630052 SAMS-608476 SAMS-630053 SAMS-607674 SAMS-607675 SAMS-631432 SAMS-237926 SAMS-231089 SAMS-235604 SAMS-291349 SAMS-603986 SAMS-235514 SAMS-631419 SAMS-631433 SAMS-631422""".split()
catalog=list_products()
by_offer={str(p.get("offer_id") or ""):p for p in catalog}
ids=[int(by_offer[o].get("product_id") or 0) for o in OFFER_IDS if o in by_offer]
products=get_product_attributes(ids)
by_product_offer={str(p.get("offer_id") or ""):p for p in products}
groups={}
rows=[]
wanted=("3006500000","7326909807","7323990000","9403208009","9403109300","9403109100","9403109809","8303009000","3926909709","3924900009","9401690000","9401790009","9403601009","9403609009")
for offer in OFFER_IDS:
 p=by_product_offer.get(offer)
 if not p:
  rows.append({"offer_id":offer,"error":"Missing from product attributes"});continue
 c=int(p.get("description_category_id") or 0);t=int(p.get("type_id") or 0)
 group=(c,t)
 if group not in groups:
  attrs=get_category_attributes(c,t)
  matches=[a for a in attrs if is_tnved_name(a.get("name"))]
  a=matches[0] if len(matches)==1 else None
  allowed={}
  if a:
   v=get_dictionary_values(c,t,int(a["id"]))
   for entry in v:
    code=str(entry.get("value") or "").split(" - ")[0].strip()
    if code in wanted:
     allowed[code]={"id":entry.get("id"),"value":str(entry.get("value") or "")[:160]}
  groups[group]={"tnved_attr":int(a["id"]) if a else None,"allowed":allowed}
 meta=groups[group]
 v=attr_value_tuple(find_product_attr(p,meta["tnved_attr"]) or {}) if meta["tnved_attr"] else None
 mark_attr=find_product_attr(p,23536)
 flags=attr_value_tuple(mark_attr or {})
 attrs=[{"id":int(a.get("id") or 0),"values":[str(z.get("value") or "")[:120] for z in a.get("values") or []][:2]} for a in p.get("attributes") or [] if a.get("values")]
 rows.append({"offer_id":offer,"name":p.get("name"),"product_id":p.get("id"),"group":[c,t],"tnved":v,"marking":flags,"allowed_codes":list(meta["allowed"]),"attributes":attrs[:28]})
print("MARKING_AUDIT_SUMMARY",json.dumps({"count":len(rows),"found":len(products),"missing_tnved":[r["offer_id"] for r in rows if not r.get("tnved")],"filled_tnved":[{"offer":r["offer_id"],"tnved":r.get("tnved")} for r in rows if r.get("tnved")]},ensure_ascii=False),flush=True)
for r in rows:
 print("MARKING_AUDIT_ROW",json.dumps(r,ensure_ascii=False),flush=True)
out={"rows":rows,"groups":[{"group":list(k),**v} for k,v in groups.items()]}
Path("ozon/marking_audit_20261118.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
