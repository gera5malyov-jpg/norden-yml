#!/usr/bin/env python3
"""One-off audited 4sis missing-photo repair executor, using temporary NetAngels key."""
import json,os,pathlib,subprocess,sys,tempfile,urllib.parse,urllib.request

VM_ID=44780
VM_IP="45.86.180.49"

def api(url,method="GET",data=None,headers=None):
    req=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(req,timeout=35) as r:
        raw=r.read().decode("utf-8")
        return json.loads(raw) if raw else {}

def main():
    mode=sys.argv[1] if len(sys.argv)==2 else ""
    if mode not in ("audit","apply"):raise RuntimeError("Must explicitly choose audit or apply")
    php=pathlib.Path(__file__).with_name("repair_4sis_cached_yml_images.php")
    api_key=os.environ["NETANGELS_API_KEY"].strip()
    token=api("https://panel.netangels.ru/api/gateway/token/","POST",
        urllib.parse.urlencode({"api_key":api_key}).encode(),
        {"Content-Type":"application/x-www-form-urlencoded"})["token"]
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json","Accept":"application/json"}
    with tempfile.TemporaryDirectory() as d:
        key=pathlib.Path(d)/"id"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",str(key)],check=True)
        pub=pathlib.Path(str(key)+".pub").read_text().strip()
        created=api(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/","POST",
          json.dumps({"key":pub,"name":"4sis-img-missing-"+os.getenv("GITHUB_RUN_ID","manual")}).encode(),headers)
        kid=created["id"]
        args=["-i",str(key),"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
              "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15"]
        remote="/tmp/4sis-extimg-recovery-"+os.getenv("GITHUB_RUN_ID","manual")+".php"
        try:
            r=subprocess.run(["scp"]+args+[str(php),"root@"+VM_IP+":"+remote],
                text=True,capture_output=True,timeout=35)
            if r.returncode:raise RuntimeError("Secure upload failed: "+r.stderr[-300:])
            cmd=f"set -eu; chown web:web {remote}; chmod 600 {remote}; su -s /bin/bash web -c 'php {remote} {mode}'"
            r=subprocess.run(["ssh"]+args+["root@"+VM_IP,cmd],
              text=True,capture_output=True,timeout=160)
            print(r.stdout[-7000:],flush=True)
            if r.returncode:raise RuntimeError("4sis recovery failed: "+r.stderr[-800:])
            if "FOURSIS_RESTORE_PLAN " not in r.stdout:
                raise RuntimeError("No source/YML match confirmation")
            if mode=="apply" and ("FOURSIS_BACKUP_OK " not in r.stdout or
                                  "FOURSIS_RESTORE_RESULT " not in r.stdout):
                raise RuntimeError("No 4sis backup/result confirmation")
        finally:
            subprocess.run(["ssh"]+args+["root@"+VM_IP,"rm -f "+remote],
              capture_output=True,timeout=30)
            try:
                api(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/",
                    "DELETE",None,headers)
            except Exception as e:
                print("SSH_KEY_CLEANUP_WARNING",type(e).__name__)

if __name__=="__main__":main()
