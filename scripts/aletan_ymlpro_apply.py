#!/usr/bin/env python3
"""Run owned-server YML Pro linkProduct() recovery with temporary SSH key."""
import json
import os
import pathlib
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request

ROOT=pathlib.Path(__file__).resolve().parents[1]
PLAN=ROOT/"aletan_ymlpro_plan.tmp.json"
PHP=ROOT/"scripts/aletan_ymlpro_apply.php"
VM_ID=44780
VM_IP="45.86.180.49"

def api(url,method="GET",data=None,headers=None):
    request=urllib.request.Request(url,method=method,headers=headers or {},data=data)
    with urllib.request.urlopen(request,timeout=35) as r:
        raw=r.read().decode("utf-8")
        return json.loads(raw) if raw else {}

def main():
    mode=os.environ.get("ALETAN_REPAIR_MODE","off")
    if mode not in ("one","all"):
        raise RuntimeError("Repair is not enabled for this run")
    plan=json.loads(PLAN.read_text(encoding="utf-8"))
    assert 750<=len(plan)<=900
    assert len({x["offer_id"] for x in plan})==len(plan)
    assert len({x["product_id"] for x in plan})==len(plan)
    assert any(x["offer_id"]=="B102" and x["product_id"]==1443656 for x in plan)
    if any(x.get("profile_id")!=373 for x in plan):
        raise RuntimeError("Unexpected profile in proposed links")
    api_key=os.environ["NETANGELS_API_KEY"].strip()
    token=api("https://panel.netangels.ru/api/gateway/token/","POST",
              urllib.parse.urlencode({"api_key":api_key}).encode(),
              {"Content-Type":"application/x-www-form-urlencoded"})["token"]
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json","Accept":"application/json"}
    with tempfile.TemporaryDirectory() as tmp:
        identity=pathlib.Path(tmp)/"id"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",str(identity)],check=True)
        pub=pathlib.Path(str(identity)+".pub").read_text().strip()
        created=api(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/","POST",
                    json.dumps({"key":pub,"name":"aletan-binding-repair-"+os.getenv("GITHUB_RUN_ID","manual")}).encode(),headers)
        kid=created["id"]
        run_id=re.sub(r"[^0-9]","",os.getenv("GITHUB_RUN_ID","0"))
        remote_plan=f"/tmp/aletan_ymlpro_plan_{run_id}.json"
        remote_php=f"/tmp/aletan_ymlpro_apply_{run_id}.php"
        ssh=["ssh","-i",str(identity),"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
             "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15"]
        scp=["scp","-i",str(identity),"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
             "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15"]
        try:
            subprocess.run(scp+[str(PLAN),f"root@{VM_IP}:{remote_plan}"],check=True,timeout=50,
                           capture_output=True)
            subprocess.run(scp+[str(PHP),f"root@{VM_IP}:{remote_php}"],check=True,timeout=50,
                           capture_output=True)
            remote=(f"set -e; chown web:web {remote_plan} {remote_php}; "
                    f"chmod 600 {remote_plan} {remote_php}; "
                    f"su -s /bin/bash web -c 'php {remote_php} {mode} {remote_plan}'")
            result=subprocess.run(ssh+["root@"+VM_IP,remote],text=True,capture_output=True,timeout=1100)
            print(result.stdout[-16000:])
            if result.returncode:
                print("REMOTE_EXIT_STATUS",result.returncode)
                if result.stderr:
                    print("REMOTE_ERROR",result.stderr[-1800:])
                raise RuntimeError("Link repair was incomplete: examine report and server backup")
            if "BACKUP_OK" not in result.stdout or "RESULT " not in result.stdout:
                raise RuntimeError("Missing backup or result acknowledgement")
        finally:
            try:
                subprocess.run(ssh+["root@"+VM_IP,
                    f"rm -f {remote_plan} {remote_php}"],timeout=25,capture_output=True)
            except Exception as e:
                print("remote_cleanup_warning",type(e).__name__)
            try:
                api(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
            except Exception as e:
                print("ssh_key_cleanup_warning",type(e).__name__)

if __name__=="__main__":
    main()
