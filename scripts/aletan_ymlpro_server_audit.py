#!/usr/bin/env python3
"""Read-only audit of Aletan YML Pro product-matching state on owned VM."""
import json, os, subprocess, tempfile, urllib.parse, urllib.request
from pathlib import Path

VM_ID=44780
VM_IP="45.86.180.49"

def api(url,method="GET",data=None,headers=None):
    request=urllib.request.Request(url,data=data,headers=headers or {},method=method)
    with urllib.request.urlopen(request,timeout=25) as r:
        result=r.read().decode("utf-8")
        return json.loads(result) if result else {}

def main():
    key=os.environ["NETANGELS_API_KEY"].strip()
    token=api("https://panel.netangels.ru/api/gateway/token/","POST",
              urllib.parse.urlencode({"api_key":key}).encode(),
              {"Content-Type":"application/x-www-form-urlencoded"})["token"]
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json","Accept":"application/json"}
    with tempfile.TemporaryDirectory() as temp:
        identity=Path(temp)/"id"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",str(identity)],check=True)
        pub=Path(str(identity)+".pub").read_text().strip()
        r=api(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/","POST",
              json.dumps({"key":pub,"name":"aletan-ymlpro-audit-"+os.getenv("GITHUB_RUN_ID","manual")}).encode(),headers)
        key_id=r["id"]
        try:
            script=r"""set -eu
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
echo '=====PLUGINS====='
ls "$ROOT/wa-apps/shop/plugins" | grep -Ei 'yml|xml' || true
echo '=====YML PLUGIN PHP PATHS====='
find "$ROOT/wa-apps/shop/plugins/" -maxdepth 3 -iname '*yml*' | head -45
echo '=====MATCHING CODE HINTS====='
for p in "$ROOT/wa-apps/shop/plugins/yml" "$ROOT/wa-apps/shop/plugins/ymlpro"; do
  test -d "$p" || continue
  grep -R -n -E 'yml_profile_id|yml_id|external_id|product_save|productSave' "$p/lib" --include='*.php' 2>/dev/null | head -65 | cut -c1-270 || true
done
echo '=====DATABASE READ ONLY====='
mysql --batch --skip-column-names -e "SELECT DATABASE();" 2>&1 | head -2
mysql --batch --skip-column-names -e "SHOW TABLES LIKE '%yml%';" 2>&1 | head -40
echo '=====COMPLETED====='
"""
            proc=subprocess.run(["ssh","-i",str(identity),"-o","BatchMode=yes",
                                  "-o","StrictHostKeyChecking=no","-o","UserKnownHostsFile=/dev/null",
                                  "-o","ConnectTimeout=12","root@"+VM_IP,"bash","-s"],
                                 input=script,text=True,capture_output=True,timeout=155)
            print(proc.stdout[-25000:])
            if proc.stderr:print("STDERR",proc.stderr[-900:])
            if proc.returncode:raise RuntimeError(f"SSH status {proc.returncode}")
        finally:
            api(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{key_id}/","DELETE",None,headers)

if __name__=="__main__":
    main()
