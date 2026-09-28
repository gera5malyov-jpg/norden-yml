#!/usr/bin/env python3
import json, os, subprocess, sys, tempfile, urllib.parse, urllib.request

API_KEY=os.environ.get("NETANGELS_API_KEY","").strip()
VM_ID=44780
VM_IP="45.86.180.49"
ROOT="/home/web/vm-23f9aff9.na4u.ru/www"
REPORT=".deploy-probe/megasuppliers-server-probe.txt"

if not API_KEY:
    raise SystemExit("NETANGELS_API_KEY is not set")

def request_json(url, method="GET", data=None, headers=None):
    req=urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw=r.read().decode("utf-8")
        return r.status, json.loads(raw) if raw else {}

def run():
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    lines=[]
    def add(s):
        lines.append(str(s))
        print(s)

    token_body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
    _,tok=request_json(
        "https://panel.netangels.ru/api/gateway/token/",
        "POST",
        token_body,
        {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"megasuppliers-probe/1.0"},
    )
    token=tok.get("token")
    if not token:
        raise RuntimeError("token missing")
    headers={
        "Authorization":f"Bearer {token}",
        "Content-Type":"application/json",
        "Accept":"application/json",
        "User-Agent":"megasuppliers-probe/1.0",
    }

    with tempfile.TemporaryDirectory() as td:
        key_path=os.path.join(td,"megasuppliers_ed25519")
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key_path],check=True)
        pub=open(key_path+".pub","r",encoding="utf-8").read().strip()
        payload=json.dumps({"key":pub,"name":"chatgpt-megasuppliers-probe-"+os.environ.get("GITHUB_RUN_ID","manual")}).encode()
        key_id=None
        try:
            _,created=request_json(
                f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/",
                "POST",payload,headers
            )
            key_id=created.get("id")
            if not key_id:
                raise RuntimeError("SSH key id missing")

            remote_cmd=f"""set -eu
ROOT={ROOT}
PLUGIN="$ROOT/wa-apps/shop/plugins/megasuppliers"
echo "hostname=$(hostname)"
echo "root_exists=$(test -d "$ROOT" && echo yes || echo no)"
ls -ld "$ROOT" "$ROOT/wa-apps" "$ROOT/wa-apps/shop" "$ROOT/wa-apps/shop/plugins" 2>&1 || true
echo "--- php ---"
php -v 2>&1 | head -n 3 || true
echo "--- plugin ---"
if [ -e "$PLUGIN" ]; then
  echo "plugin_exists=yes"
  ls -ld "$PLUGIN"
  find "$PLUGIN" -maxdepth 2 -type f -printf '%u:%g %m %p\\n' | head -n 30
else
  echo "plugin_exists=no"
fi
echo "--- plugin config registration ---"
if [ -f "$ROOT/wa-config/apps/shop/plugins.php" ]; then
  grep -n "megasuppliers" "$ROOT/wa-config/apps/shop/plugins.php" || true
  ls -l "$ROOT/wa-config/apps/shop/plugins.php"
  echo "--- plugins.php contents ---"
  cat "$ROOT/wa-config/apps/shop/plugins.php"
else
  echo "plugins.php not found"
fi
echo "--- existing plugin ownership sample ---"
find "$ROOT/wa-apps/shop/plugins" -mindepth 1 -maxdepth 1 -type d -printf '%u:%g %m %p\\n' | head -n 12
echo "--- web user php modules ---"
su -s /bin/bash web -c 'php -m | grep -E "mysqli|pdo_mysql|zip|SimpleXML|mbstring" || true'
echo "--- nordenstock config ---"
for f in "$ROOT/wa-apps/shop/plugins/nordenstock/lib/config/plugin.php" "$ROOT/wa-apps/shop/plugins/nordenstock/lib/config/db.php" "$ROOT/wa-apps/shop/plugins/nordenstock/lib/config/install.php"; do
  if [ -f "$f" ]; then echo "### $f"; sed -n '1,260p' "$f"; fi
done
echo "--- sample plugin backend actions ---"
find "$ROOT/wa-apps/shop/plugins" -path '*/lib/actions/backend/*.php' -type f | head -n 5 | while read f; do echo "### $f"; sed -n '1,180p' "$f"; done
echo "--- framework plugin installer references ---"
grep -RIn "function.*install.*Plugin\|install.php\|lib/config/db.php" "$ROOT/wa-system" "$ROOT/wa-installer/lib" 2>/dev/null | head -n 120 || true
echo "--- waPlugin install implementation ---"
sed -n '1,310p' "$ROOT/wa-system/plugin/waPlugin.class.php"
echo "--- create plugin CLI install helper ---"
sed -n '140,235p' "$ROOT/wa-system/webasyst/lib/cli/webasystCreatePlugin.cli.php"
echo "--- shop getPlugin references ---"
grep -RIn "getPlugin(" "$ROOT/wa-apps/shop/lib" 2>/dev/null | head -n 60 || true
"""
            ok=False
            for user in ("root","web"):
                p=subprocess.run([
                    "ssh","-i",key_path,
                    "-o","BatchMode=yes",
                    "-o","StrictHostKeyChecking=no",
                    "-o","UserKnownHostsFile=/dev/null",
                    "-o","ConnectTimeout=12",
                    f"{user}@{VM_IP}","bash -s"
                ],input=remote_cmd,text=True,capture_output=True,timeout=40)
                add(f"ssh_user={user} exit={p.returncode}")
                if p.stdout:
                    add(p.stdout)
                if p.stderr:
                    add("stderr="+p.stderr[-800:])
                if p.returncode==0:
                    ok=True
                    break
            if not ok:
                raise RuntimeError("No SSH user succeeded")
        finally:
            if key_id:
                try:
                    request_json(
                        f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{key_id}/",
                        "DELETE",None,headers
                    )
                    add("temp_key_removed=yes")
                except Exception as e:
                    add("WARNING temp_key_removed=no "+repr(e))

    open(REPORT,"w",encoding="utf-8").write("\n".join(lines)+"\n")

if __name__=="__main__":
    try:
        run()
    except Exception as e:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT,"a",encoding="utf-8") as f:
            f.write("ERROR: "+repr(e)+"\n")
        print("ERROR:",repr(e),file=sys.stderr)
