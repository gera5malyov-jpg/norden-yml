#!/usr/bin/env python3
"""Fallback: execute Artikul public-web scraping from user's NetAngels VM.

All files and SSH keys are temporary. Does not touch the Webasyst site.
GitHub-hosted runners may receive ECONNREFUSED from artikul-mebel.ru.
"""
import json, os, shlex, subprocess, tempfile, urllib.parse, urllib.request, uuid
from pathlib import Path

VM_ID=44780
VM_IP="45.86.180.49"
SRC=Path("artikul-mebel")
API=os.environ.get("NETANGELS_API_KEY","").strip()

def req(url,method="GET",data=None,headers=None):
    r=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(r,timeout=30) as h:
        raw=h.read().decode("utf-8","replace")
        return h.status,json.loads(raw) if raw else {}

def call(args,timeout=300):
    subprocess.run(args,check=True,timeout=timeout)

def main():
    if not API: raise RuntimeError("NETANGELS_API_KEY not configured; YML not changed")
    payload=urllib.parse.urlencode({"api_key":API}).encode()
    _,t=req("https://panel.netangels.ru/api/gateway/token/","POST",payload,{"Content-Type":"application/x-www-form-urlencoded"})
    auth={"Authorization":"Bearer "+t["token"],"Accept":"application/json","Content-Type":"application/json"}
    with tempfile.TemporaryDirectory() as td:
        key=Path(td)/"artikul_ed25519"
        call(["ssh-keygen","-q","-t","ed25519","-N","","-f",str(key)],30)
        pub=(key.with_suffix(".pub")).read_text().strip()
        key_id=None
        remote="/tmp/artikul-yml-"+uuid.uuid4().hex[:12]
        common=["-i",str(key),"-o","BatchMode=yes","-o","StrictHostKeyChecking=accept-new",
                "-o","UserKnownHostsFile="+str(Path(td)/"known_hosts"),"-o","ConnectTimeout=15"]
        ssh=["ssh",*common,"root@"+VM_IP]
        scp=["scp",*common]
        try:
            _,created=req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/",
                          "POST",json.dumps({"key":pub,"name":"chatgpt-artikul-weekly-temp"}).encode(),auth)
            key_id=created.get("id")
            if not key_id: raise RuntimeError("Temporary SSH key not accepted")
            # The server is used only as a network vantage point and ephemeral execution.
            call(ssh+["mkdir -p "+shlex.quote(remote)],60)
            files=["parser.py","parser_core.py","requirements.txt","catalog.yml"]
            for fn in files:
                call(scp+[str(SRC/fn),"root@"+VM_IP+":"+remote+"/"+fn],90)
            script=(
                "set -eu; cd "+shlex.quote(remote)+"; "
                "python3 -m venv venv; "
                "./venv/bin/python -m pip install -q 'requests>=2.32,<3' 'beautifulsoup4>=4.12,<5'; "
                "echo 'REMOTE_CATALOG_CHECK:'; "
                "./venv/bin/python -c \"import requests; "
                "r=requests.get('https://artikul-mebel.ru/sitemap.xml',timeout=20); "
                "print(r.status_code, len(r.content)); r.raise_for_status()\"; "
                "ARTIKUL_VALIDATE_EXISTING=1 MIN_PRODUCTS=100 WORKERS=4 OUTPUT=catalog.yml "
                "./venv/bin/python -u parser.py"
            )
            call(ssh+[script],timeout=6800)
            call(scp+["root@"+VM_IP+":"+remote+"/catalog.yml",str(SRC/"catalog.yml")],120)
            print("Remote catalog generated; ready for local validation/publishing",flush=True)
        finally:
            if key_id:
                try:
                    call(ssh+["rm -rf -- "+shlex.quote(remote)],timeout=60)
                except Exception as e:
                    print("WARN: failed temporary remote directory cleanup:",str(e)[:200])
                try:
                    req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{key_id}/","DELETE",None,auth)
                    print("Temporary SSH key deleted",flush=True)
                except Exception as e:
                    print("WARNING: SSH key cleanup failed:",str(e)[:200])

if __name__=="__main__":
    main()
