#!/usr/bin/env python3
import json, os, urllib.parse, urllib.request, urllib.error

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780

def req(url, method="GET", data=None, headers=None):
    r=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(r,timeout=30) as x:
        raw=x.read().decode("utf-8")
        try:
            body=json.loads(raw) if raw else None
        except Exception:
            body=raw
        return x.status, body

body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
_,tok=req("https://panel.netangels.ru/api/gateway/token/","POST",body,
          {"Content-Type":"application/x-www-form-urlencoded"})
headers={"Authorization":"Bearer "+tok["token"],"Content-Type":"application/json","Accept":"application/json"}
base=f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/"
status,data=req(base,"GET",None,headers)
print("list_status",status)\nprint("payload_type",type(data).__name__)\nprint("payload_keys",sorted(list(data.keys())) if isinstance(data,dict) else [])

if isinstance(data,list):
    items=data
elif isinstance(data,dict):
    items=data.get("results") or data.get("items") or data.get("data") or []
else:
    items=[]

prefixes=(
    "chatgpt-megasuppliers",
    "chatgpt-ms-ui-diag",
    "chatgpt-ms-final-check",
)
print("keys_total",len(items))
removed=0
for item in items:
    if not isinstance(item,dict):
        continue
    name=str(item.get("name") or "")
    kid=item.get("id")
    if kid is None or not name.startswith(prefixes):
        continue
    try:
        st,_=req(base+str(kid)+"/","DELETE",None,headers)
        print("removed",kid,name,st)
        removed+=1
    except urllib.error.HTTPError as e:
        print("remove_failed",kid,name,e.code)
print("removed_total",removed)
