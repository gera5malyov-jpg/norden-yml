#!/usr/bin/env python3
import json, os, sys, urllib.parse, urllib.request

API_KEY=os.environ.get("NETANGELS_API_KEY","").strip()
if not API_KEY:
    print("NETANGELS_API_KEY is not set", file=sys.stderr); sys.exit(2)

def req_json(url, method="GET", data=None, headers=None):
    req=urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode("utf-8"))

body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
_,tok=req_json("https://panel.netangels.ru/api/gateway/token/","POST",body,{"Content-Type":"application/x-www-form-urlencoded","User-Agent":"browser-mcp-vps-probe/1.0"})
token=tok.get("token")
if not token:
    print("token missing", file=sys.stderr); sys.exit(3)

status,payload=req_json("https://api-ms.netangels.ru/api/v1/cloud/vms/?limit=100",headers={"Authorization":f"Bearer {token}","Accept":"application/json","User-Agent":"browser-mcp-vps-probe/1.0"})
items=payload.get("entities", payload if isinstance(payload,list) else [])
print(f"status={status} vm_count={len(items)}")
for vm in items:
    safe={k:vm.get(k) for k in ("id","uid","name","hostname","main_ip","lan_ip","tariff","is_managed","created","updated") if k in vm}
    print(json.dumps(safe, ensure_ascii=False))
