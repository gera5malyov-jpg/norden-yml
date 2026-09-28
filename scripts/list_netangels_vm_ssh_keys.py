#!/usr/bin/env python3
import json, os, urllib.parse, urllib.request
from pathlib import Path

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780
OUT=Path(".deploy-probe/netangels-vm-ssh-keys.txt")

def req(url, method="GET", data=None, headers=None):
    r=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(r,timeout=30) as x:
        raw=x.read().decode("utf-8")
        return x.status, json.loads(raw) if raw else {}

body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
_,tok=req("https://panel.netangels.ru/api/gateway/token/","POST",body,
          {"Content-Type":"application/x-www-form-urlencoded"})
headers={"Authorization":"Bearer "+tok["token"],"Accept":"application/json"}
_,vms=req("https://api-ms.netangels.ru/api/v1/cloud/vms/?limit=100","GET",None,headers)
vm_items=vms.get("entities",[]) if isinstance(vms,dict) else []
vm=next((x for x in vm_items if int(x.get("id",0))==VM_ID),{})
print("vm_state="+str(vm.get("state","unknown")))
_,data=req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/?limit=100","GET",None,headers)
items=data.get("entities",[]) if isinstance(data,dict) else data
lines=["vm_key_count="+str(len(items))]
for item in items:
    lines.append("vm id={id} name={name} created={created} fingerprint={fingerprint}".format(
        id=item.get("id"), name=item.get("name",""), created=item.get("created",""), fingerprint=item.get("fingerprint","")
    ))

_,global_data=req("https://api-ms.netangels.ru/api/v1/sshkeys/?limit=100","GET",None,headers)
global_items=global_data.get("entities",[]) if isinstance(global_data,dict) else global_data
lines.append("account_key_count="+str(len(global_items)))
for item in global_items:
    lines.append("account id={id} name={name} created={created} fingerprint={fingerprint}".format(
        id=item.get("id"), name=item.get("name",""), created=item.get("created",""), fingerprint=item.get("fingerprint","")
    ))
OUT.parent.mkdir(exist_ok=True)
OUT.write_text("\n".join(lines)+"\n",encoding="utf-8")
print("\n".join(lines))
