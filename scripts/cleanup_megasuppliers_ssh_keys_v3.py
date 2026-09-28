#!/usr/bin/env python3
import json
import os
import urllib.parse
import urllib.request
import urllib.error

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
PREFIXES=(
    "chatgpt-megasuppliers-install-",
    "chatgpt-megasuppliers-probe-",
    "chatgpt-megasuppliers-backfill",
    "chatgpt-megasuppliers-two-tests",
    "chatgpt-ms-final-check",
    "chatgpt-ms-ui-diag",
)

def open_req(url, method="GET", data=None, headers=None):
    req=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    try:
        with urllib.request.urlopen(req,timeout=30) as resp:
            raw=resp.read().decode("utf-8","replace")
            return resp.status, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8","replace")

status, raw=open_req(
    "https://panel.netangels.ru/api/gateway/token/",
    "POST",
    urllib.parse.urlencode({"api_key":API_KEY}).encode(),
    {"Content-Type":"application/x-www-form-urlencoded"}
)
if status != 200:
    raise SystemExit("token_error="+str(status))
token=json.loads(raw)["token"]
auth={"Authorization":"Bearer "+token,"Accept":"application/json"}

base="https://api-ms.netangels.ru/api/v1/sshkeys/"
status, raw=open_req(base+"?limit=100","GET",None,auth)
print("list_status="+str(status))
if status != 200:
    raise SystemExit("list_failed")
data=json.loads(raw) if raw else {}
items=data.get("entities",[]) if isinstance(data,dict) else []

targets=[
    x for x in items
    if isinstance(x,dict)
    and any(str(x.get("name") or "").startswith(p) for p in PREFIXES)
]
print("account_total="+str(len(items)))
print("task_targets="+str(len(targets)))

removed=0
failed=0
for item in targets:
    kid=item.get("id")
    name=str(item.get("name") or "")
    status, body=open_req(base+str(kid)+"/","DELETE",None,auth)
    if status == 200:
        removed += 1
        print("removed="+str(kid)+" "+name)
    else:
        failed += 1
        print("remove_failed="+str(kid)+" "+name+" status="+str(status)+" body="+body[:300].replace("\n"," "))

status, raw=open_req(base+"?limit=100","GET",None,auth)
remaining_data=json.loads(raw) if status==200 and raw else {}
remaining=remaining_data.get("entities",[]) if isinstance(remaining_data,dict) else []
remaining_task=[
    x for x in remaining
    if isinstance(x,dict)
    and any(str(x.get("name") or "").startswith(p) for p in PREFIXES)
]
print("removed_total="+str(removed))
print("failed_total="+str(failed))
print("remaining_task_keys="+str(len(remaining_task)))
