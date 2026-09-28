#!/usr/bin/env python3
import json
import os
import urllib.parse
import urllib.request
import urllib.error

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780
STALE_NAME="chatgpt-ms-final-check"

def open_req(url, method="GET", data=None, headers=None):
    req=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    try:
        with urllib.request.urlopen(req,timeout=30) as resp:
            raw=resp.read().decode("utf-8","replace")
            return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw=exc.read().decode("utf-8","replace")
        return exc.code, raw

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

status, raw=open_req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/","GET",None,auth)
print("list_status="+str(status))
data=json.loads(raw) if raw else {}
items=data.get("entities",[]) if isinstance(data,dict) else []
target=next((x for x in items if isinstance(x,dict) and x.get("name")==STALE_NAME),None)
if not target:
    print("stale_present=no")
    raise SystemExit(0)
kid=target.get("id")
print("stale_present=yes")
print("stale_id="+str(kid))
status, raw=open_req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,auth)
print("delete_status="+str(status))
if raw:
    try:
        payload=json.loads(raw)
        if isinstance(payload,dict):
            safe={k:v for k,v in payload.items() if k not in ("key","token","api_key")}
            print("delete_body="+json.dumps(safe,ensure_ascii=False))
        else:
            print("delete_body_type="+type(payload).__name__)
    except Exception:
        print("delete_body_text="+raw[:1000].replace("\n"," "))
