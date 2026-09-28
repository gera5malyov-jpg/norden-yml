#!/usr/bin/env python3
import json, os, urllib.parse, urllib.request
API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780

def req(url, method="GET", data=None, headers=None):
    r=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(r,timeout=30) as x:
        raw=x.read().decode("utf-8")
        return x.status, json.loads(raw) if raw else {}

_,tok=req(
    "https://panel.netangels.ru/api/gateway/token/",
    "POST",
    urllib.parse.urlencode({"api_key":API_KEY}).encode(),
    {"Content-Type":"application/x-www-form-urlencoded"}
)
headers={"Authorization":"Bearer "+tok["token"],"Accept":"application/json"}
_,data=req("https://api-ms.netangels.ru/api/v1/cloud/vms/","GET",None,headers)
items=data.get("entities",[]) if isinstance(data,dict) else []
vm=next((x for x in items if isinstance(x,dict) and int(x.get("id",0))==VM_ID),None)
if not vm:
    print("vm_found=no")
else:
    print("vm_found=yes")
    print("state="+str(vm.get("state")))
    print("name="+str(vm.get("name")))
    print("hostname="+str(vm.get("hostname")))
    print("transitions="+json.dumps(vm.get("transitions"),ensure_ascii=False)[:2000])
