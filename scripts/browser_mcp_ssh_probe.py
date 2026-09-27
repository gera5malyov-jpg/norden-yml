#!/usr/bin/env python3
import json, os, subprocess, sys, tempfile, urllib.parse, urllib.request, urllib.error

API_KEY=os.environ.get("NETANGELS_API_KEY","").strip()
VM_ID=44780
VM_IP="45.86.180.49"
if not API_KEY:
    print("NETANGELS_API_KEY is not set", file=sys.stderr); sys.exit(2)

def request_json(url, method="GET", data=None, headers=None):
    req=urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw=r.read().decode("utf-8")
        return r.status, json.loads(raw) if raw else {}

token_body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
_,tok=request_json("https://panel.netangels.ru/api/gateway/token/","POST",token_body,{"Content-Type":"application/x-www-form-urlencoded","User-Agent":"browser-mcp-ssh-probe/1.0"})
token=tok.get("token")
if not token:
    raise SystemExit("token missing")
headers={"Authorization":f"Bearer {token}","Content-Type":"application/json","Accept":"application/json","User-Agent":"browser-mcp-ssh-probe/1.0"}

with tempfile.TemporaryDirectory() as td:
    key_path=os.path.join(td,"browser_mcp_ed25519")
    subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key_path],check=True)
    pub=open(key_path+".pub","r",encoding="utf-8").read().strip()
    payload=json.dumps({"key":pub,"name":"chatgpt-browser-mcp-temporary"}).encode()
    key_id=None
    try:
        status,created=request_json(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/","POST",payload,headers)
        key_id=created.get("id")
        print(f"temp_key_uploaded={bool(key_id)} status={status}")
        if not key_id:
            raise RuntimeError(f"SSH key id missing: {created}")

        remote_cmd="id; uname -a; echo OS:; sed -n '1,8p' /etc/os-release 2>/dev/null || true; echo TOOLS:; command -v docker || true; command -v node || true; command -v npm || true; echo DISK:; df -h / | tail -1"
        ok=False
        for user in ("root","web","ubuntu","debian"):
            p=subprocess.run([
                "ssh","-i",key_path,
                "-o","BatchMode=yes",
                "-o","StrictHostKeyChecking=no",
                "-o","UserKnownHostsFile=/dev/null",
                "-o","ConnectTimeout=12",
                f"{user}@{VM_IP}",remote_cmd
            ],text=True,capture_output=True,timeout=25)
            print(f"ssh_user={user} exit={p.returncode}")
            if p.stdout:
                print(p.stdout)
            if p.returncode==0:
                print(f"SSH_OK user={user}")
                ok=True
                break
            if p.stderr:
                print("ssh_error="+p.stderr.splitlines()[-1][:300])
        if not ok:
            raise RuntimeError("No SSH user succeeded")
    finally:
        if key_id:
            try:
                status,_=request_json(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{key_id}/","DELETE",None,headers)
                print(f"temp_key_removed=True status={status}")
            except Exception as e:
                print(f"WARNING: temp key removal failed: {e}",file=sys.stderr)
