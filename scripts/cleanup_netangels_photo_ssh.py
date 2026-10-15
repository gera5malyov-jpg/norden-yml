#!/usr/bin/env python3
"""Remove only an abandoned temporary SSH key from a completed CI run.

Permanent SSH keys are never modified.
"""
import os,json,urllib.parse,urllib.error
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage1_readonly_audit import request_json,VM_ID
secret=os.environ.get("NETANGELS_API_KEY","").strip()
if not secret:raise RuntimeError("NetAngels connection missing")
gateway=request_json("https://panel.netangels.ru/api/gateway/token/","POST",
 urllib.parse.urlencode({"api_key":secret}).encode(),{"Content-Type":"application/x-www-form-urlencoded"})
token=gateway.get("token")
if not token:raise RuntimeError("Token failure")
headers={"Authorization":"Bearer "+token,"Content-Type":"application/json","Accept":"application/json"}
url="https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/".format(VM_ID)
data=request_json(url,"GET",None,headers)
rows=data.get("entities",[]) if isinstance(data,dict) else []
target=[x for x in rows if isinstance(x,dict) and x.get("id")==37470 and
        str(x.get("name","")).startswith("storefront-stage52-38790887053_")]
if len(target)!=1:
    print("SSH_CLEANUP=target_absent_or_changed")
    exit(0)
try:
    request_json(url+"37470/","DELETE",None,headers)
except urllib.error.HTTPError as err:
    message=err.read(1200).decode("utf-8","replace")
    print("SSH_DELETE_DIAGNOSTIC="+json.dumps({"http":err.code,"message":message[:480]},ensure_ascii=False))
    raise
print("SSH_CLEANUP=retired_abandoned_temp_key")
fresh=request_json(url,"GET",None,headers)
exists=any(isinstance(x,dict) and x.get("id")==37470 for x in fresh.get("entities",[]))
print("SSH_CLEANUP_VERIFIED="+str(not exists).lower())
if exists:raise RuntimeError("Temporary key was not removed")
