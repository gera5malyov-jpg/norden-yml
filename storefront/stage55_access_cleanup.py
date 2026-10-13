"""Reconcile only the temporary key from the known interrupted rollout.

The failed POST returned 502 before an SSH connection. Do not retry that POST
or remove unrelated keys. List API: NetAngels cloud.sshkeys documentation.
"""
import json,os,urllib.parse
from datetime import datetime,timezone
from stage1_readonly_audit import request_json,VM_ID

NAME='storefront-stage52-38377653546'
START=datetime(2026,10,13,9,35,tzinfo=timezone.utc)
END=datetime(2026,10,13,9,39,tzinfo=timezone.utc)

def main():
 secret=os.environ.get('NETANGELS_API_KEY','').strip()
 if not secret:raise RuntimeError('Missing NetAngels API key')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',urllib.parse.urlencode({'api_key':secret}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('Gateway token unavailable')
 headers={'Authorization':'Bearer '+token,'Accept':'application/json'}
 base='https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/'.format(VM_ID)
 def matches():
  result=[]
  for offset in range(0,10000,100):
   payload=request_json(base+'?limit=100&offset='+str(offset),headers=headers)
   rows=payload if isinstance(payload,list) else payload.get('entities')
   if not isinstance(rows,list):raise RuntimeError('Unknown SSH inventory schema; no cleanup')
   for row in rows:
    if row.get('name')!=NAME:continue
    created=datetime.fromisoformat(str(row.get('created','')).replace('Z','+00:00'))
    if created.tzinfo is None:raise RuntimeError('Ambiguous key creation time; no cleanup')
    if not START<=created<=END:raise RuntimeError('Unexpected key creation time; no cleanup')
    result.append(row)
   if len(rows)<100:return result
  raise RuntimeError('Incomplete SSH inventory; no cleanup')
 rows=matches()
 if len(rows)>1:raise RuntimeError('Ambiguous interrupted key; no cleanup')
 for row in rows:request_json(base+str(int(row['id']))+'/',method='DELETE',headers=headers)
 if matches():raise RuntimeError('Interrupted temporary access remains')
 print('STAGE55_ACCESS_CLEANUP='+json.dumps({'removed':len(rows),'unrelated_keys_changed':False}))

if __name__=='__main__':main()
