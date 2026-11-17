#!/usr/bin/env python3
"""Read-only inspection of Ozon TN VED dictionary for 2026-11-17 unresolved SKUs."""
import json
from tnved_sync import get_category_attributes,get_dictionary_values,is_tnved_name
queries={
 (80731485,95098):["9403208009"],
 (17029020,94491):["3926909709"],
 (85282223,970996040):["9402900000"],
 (17027919,95108):["9403500009","9403609009","9403105800"],
 (17028714,970855518):["7326909807","7323990000"],
 (17028690,98241):["8303004000","8303009000"],
}
for group,wanted in queries.items():
  try:
    attrs=[x for x in get_category_attributes(*group) if is_tnved_name(x.get('name'))]
    if len(attrs)!=1:
      print('GROUP_ERROR',group,'TN VED metadata',len(attrs),flush=True);continue
    vals=get_dictionary_values(*group,int(attrs[0]['id']))
    options={code:[{'id':x['id'],'value':x['value']} for x in vals
                   if str(x.get('value') or '').split(' - ')[0].strip()==code] for code in wanted}
    print('GROUP',group,'COUNT',len(vals),'OPTIONS',json.dumps(options,ensure_ascii=False),flush=True)
  except Exception as exc:
    print('GROUP_ERROR',group,str(exc),flush=True)
