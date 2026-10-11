#!/usr/bin/env python3
"""Read-only diagnosis of storefront VM availability through authorized NetAngels API."""
import os,json,urllib.parse,urllib.error
from stage1_readonly_audit import VM_ID,request_json

key=os.environ.get('NETANGELS_API_KEY','').strip()
if not key:raise RuntimeError('Missing NetAngels secret')
token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
 urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
if not token:raise RuntimeError('No NetAngels token')
headers={'Authorization':'Bearer '+token,'Accept':'application/json'}
urls=[
 'https://api-ms.netangels.ru/api/v1/cloud/vms/{}/'.format(VM_ID),
 'https://api-ms.netangels.ru/api/v1/cloud/vms/',
]
for url in urls:
 try:
  result=request_json(url,'GET',None,headers)
  if isinstance(result,list):
   entries=result
  elif isinstance(result,dict) and isinstance(result.get('results'),list):
   entries=result['results']
  else:entries=[result]
  selected=[]
  for obj in entries:
   if not isinstance(obj,dict):continue
   if '44780' in str(obj.get('id','')) or len(entries)==1:
    selected.append({k:v for k,v in obj.items()
      if k in ('id','name','hostname','state','status','is_active','active','enabled','is_enabled','power_state','ip','ipv4','public_ip')})
  print('STAGE42_VM='+json.dumps({'url_type':'single' if '/44780/' in url else 'list','total':len(entries),'selected':selected[:8]},ensure_ascii=False),flush=True)
 except urllib.error.HTTPError as e:
  print('STAGE42_HTTP='+json.dumps({'status':e.code,'route':'single' if '/44780/' in url else 'list'},ensure_ascii=False))
