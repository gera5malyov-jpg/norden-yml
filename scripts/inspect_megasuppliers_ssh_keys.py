#!/usr/bin/env python3
import json
import os
import urllib.parse
import urllib.request
import urllib.error

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780

def req(url, method="GET", data=None, headers=None):
    request=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(request,timeout=30) as response:
        raw=response.read().decode("utf-8")
        return response.status, (json.loads(raw) if raw else None)

token_body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
_,token_data=req(
    "https://panel.netangels.ru/api/gateway/token/",
    "POST",
    token_body,
    {"Content-Type":"application/x-www-form-urlencoded"}
)
headers={
    "Authorization":"Bearer "+token_data["token"],
    "Content-Type":"application/json",
    "Accept":"application/json"
}
base=f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/"
status,data=req(base,"GET",None,headers)
print("list_status",status)
print("payload_type",type(data).__name__)
if isinstance(data,dict):
    print("payload_keys",",".join(sorted(str(x) for x in data.keys())))
else:
    print("payload_keys","")

items=[]
if isinstance(data,list):
    items=data
elif isinstance(data,dict):
    for key in ("results","items","data","ssh","keys","entities"):
        value=data.get(key)
        if isinstance(value,list):
            items=value
            break
    if not items and all(isinstance(v,dict) for v in data.values()):
        items=list(data.values())

prefixes=("chatgpt-megasuppliers","chatgpt-ms-ui-diag","chatgpt-ms-final-check")
print("parsed_items",len(items))
for item in items:
    if isinstance(item,dict):
        print("item",item.get("id"),item.get("name"),item.get("created"),item.get("updated"))
removed=0
for item in items:
    if not isinstance(item,dict):
        continue
    name=str(item.get("name") or "")
    kid=item.get("id")
    if kid is None or not name.startswith(prefixes):
        continue
    try:
        delete_status,_=req(base+str(kid)+"/","DELETE",None,headers)
        print("removed",kid,name,delete_status)
        removed+=1
    except urllib.error.HTTPError as exc:
        print("remove_failed",kid,name,exc.code)
print("removed_total",removed)
