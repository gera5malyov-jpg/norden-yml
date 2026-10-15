#!/usr/bin/env python3
"""Inspect NetAngels temporary SSH key counts without revealing any key material."""
import json,os,urllib.parse,urllib.error
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage1_readonly_audit import request_json,VM_ID
secret=os.environ.get("NETANGELS_API_KEY","").strip()
if not secret:raise RuntimeError("Missing saved account integration")
gateway=request_json("https://panel.netangels.ru/api/gateway/token/","POST",
 urllib.parse.urlencode({"api_key":secret}).encode(),{"Content-Type":"application/x-www-form-urlencoded"})
token=gateway.get("token")
if not token:raise RuntimeError("No gateway token")
headers={"Authorization":"Bearer "+token,"Accept":"application/json"}
url="https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/".format(VM_ID)
try:
    data=request_json(url,"GET",None,headers)
    rows=data if isinstance(data,list) else (next((v for v in data.values() if isinstance(v,list)),[]) if isinstance(data,dict) else [])
    if not isinstance(rows,list):rows=[]
    temp=[{"id":x.get("id"),"name":x.get("name","")} for x in rows if isinstance(x,dict) and str(x.get("name","")).startswith("storefront-stage52-")]
    print("NETANGELS_SSH_AUDIT="+json.dumps({"response_type":type(data).__name__,"response_keys":list(data.keys())[:12] if isinstance(data,dict) else [],
      "total_entries":len(rows),"ephemeral_stage52":len(temp),"ephemeral_ids":temp[:40]}),flush=True)
except urllib.error.HTTPError as err:
    msg=err.read(1000).decode("utf-8","replace")
    print("NETANGELS_SSH_LIST_ERROR="+json.dumps({"http":err.code,"message":msg[:260]}))
    raise
