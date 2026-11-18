#!/usr/bin/env python3
"""Read-only Ozon TN VED dictionary validation for marking page 37 flagged SKUs."""
import json
from tnved_sync import get_category_attributes,get_dictionary_values,is_tnved_name
groups=[
(52619056,95568),(17028714,970855518),(44082285,970848956),
(80770870,95092),(17027916,95053),(17027937,95474),
(80770870,95009)]
codes=["3006500000","7326909807","7323990000","8303009000","9403601009",
"9401710009","9401790009","9401690000","9403208009","3926909709",
"3924900009","8302500000","8304000000","9403109100","9403109300","9403609009"]
for g in groups:
 try:
  attrs=[x for x in get_category_attributes(*g) if is_tnved_name(x.get('name'))]
  if len(attrs)!=1:
   print("DICT_ERROR",g,"tnved_attrs",len(attrs),flush=True);continue
  attr=attrs[0]
  vals=get_dictionary_values(*g,int(attr['id']))
  matches={c:[{"id":v.get("id"),"value":v.get("value")} for v in vals if str(v.get("value") or "").split(" - ")[0].strip()==c] for c in codes}
  print("MARKING_DICTIONARY",json.dumps({"group":g,"attribute_id":attr["id"],"required":attr.get("is_required"),"total":len(vals),"selected":{k:v for k,v in matches.items() if v}},ensure_ascii=False),flush=True)
 except Exception as e:
  print("DICT_ERROR",g,str(e),flush=True)
