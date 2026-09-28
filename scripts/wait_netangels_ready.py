#!/usr/bin/env python3
import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780
STALE_NAMES={"chatgpt-ms-final-check","chatgpt-ms-ui-diag"}
MAX_WAIT_SECONDS=1200
POLL_SECONDS=15

def request(url, method="GET", data=None, headers=None):
    req=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    try:
        with urllib.request.urlopen(req,timeout=30) as resp:
            raw=resp.read().decode("utf-8","replace")
            return resp.status, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8","replace")

status, raw=request(
    "https://panel.netangels.ru/api/gateway/token/",
    "POST",
    urllib.parse.urlencode({"api_key":API_KEY}).encode(),
    {"Content-Type":"application/x-www-form-urlencoded"}
)
if status != 200:
    raise SystemExit("token_error="+str(status))
token=json.loads(raw)["token"]
auth={"Authorization":"Bearer "+token,"Accept":"application/json"}

def vm_state():
    status, raw=request("https://api-ms.netangels.ru/api/v1/cloud/vms/","GET",None,auth)
    if status != 200:
        raise RuntimeError("vm_list_status="+str(status))
    data=json.loads(raw)
    vm=next((x for x in data.get("entities",[]) if int(x.get("id",0))==VM_ID),None)
    if not vm:
        raise RuntimeError("vm_not_found")
    return str(vm.get("state"))

deadline=time.time()+MAX_WAIT_SECONDS
while True:
    state=vm_state()
    print("vm_state="+state, flush=True)
    if state=="Active":
        break
    if state in ("Stopped","StoppedByAdmin","StoppedByService","Error"):
        raise SystemExit("vm_not_active="+state)
    if time.time()>=deadline:
        raise SystemExit("vm_active_timeout="+state)
    time.sleep(POLL_SECONDS)

status, raw=request(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/","GET",None,auth)
if status != 200:
    raise SystemExit("ssh_list_status="+str(status))
data=json.loads(raw)
items=data.get("entities",[]) if isinstance(data,dict) else []
for item in items:
    if not isinstance(item,dict) or item.get("name") not in STALE_NAMES:
        continue
    key_id=item.get("id")
    status, body=request(
        f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{key_id}/",
        "DELETE",
        None,
        auth
    )
    print("stale_key_delete="+str(key_id)+":"+str(status), flush=True)
    if status not in (200,404):
        safe=body[:500].replace("\n"," ")
        raise SystemExit("stale_key_delete_failed="+str(status)+":"+safe)

print("netangels_ready=yes", flush=True)
