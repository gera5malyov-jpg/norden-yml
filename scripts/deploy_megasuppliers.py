#!/usr/bin/env python3
import io, json, os, subprocess, tempfile, time, urllib.parse, urllib.request, zipfile, hashlib, shlex
from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
GSA=os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
DRIVE_FILE_ID=os.environ["DRIVE_FILE_ID"]
EXPECTED=os.environ["EXPECTED_SHA256"]
VM_ID=44780
VM_IP="45.86.180.49"
ROOT="/home/web/vm-23f9aff9.na4u.ru/www"
REPORT=Path(".deploy-probe/megasuppliers-install.txt")

def req_json(url, method="GET", data=None, headers=None):
    r=urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(r,timeout=30) as x:
        raw=x.read().decode("utf-8")
        return x.status, json.loads(raw) if raw else {}

def ssh(key, command, stdin=None, check=True, timeout=120):
    p=subprocess.run([
        "ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
        "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15",
        "root@"+VM_IP,command
    ],input=stdin,text=True,capture_output=True,timeout=timeout)
    if check and p.returncode:
        raise RuntimeError("remote command failed: STDOUT="+p.stdout[-3000:]+" STDERR="+p.stderr[-3000:])
    return p

def main():
    REPORT.parent.mkdir(exist_ok=True)
    lines=[]
    def log(s):
        s=str(s); lines.append(s); print(s)

    # Download exact package from the user's Drive using the existing service-account secret.
    info=json.loads(GSA)
    creds=service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    svc=build("drive","v3",credentials=creds,cache_discovery=False)
    pkg="/tmp/megasuppliers-1.0.1.zip"
    req=svc.files().get_media(fileId=DRIVE_FILE_ID)
    with io.FileIO(pkg,"wb") as fh:
        dl=MediaIoBaseDownload(fh,req)
        done=False
        while not done:
            _,done=dl.next_chunk()
    digest=hashlib.sha256(Path(pkg).read_bytes()).hexdigest()
    log("package_sha256="+digest)
    if digest != EXPECTED:
        raise RuntimeError("package checksum mismatch")
    with zipfile.ZipFile(pkg) as z:
        names=z.namelist()
        if not names or any((not n.startswith("megasuppliers/")) or ".." in Path(n).parts for n in names):
            raise RuntimeError("unsafe package layout")
    log("package_verified=yes")

    # Temporary SSH key through NetAngels API.
    token_body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
    _,tok=req_json("https://panel.netangels.ru/api/gateway/token/","POST",token_body,
                   {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"megasuppliers-deploy/1.0"})
    token=tok["token"]
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json","Accept":"application/json",
             "User-Agent":"megasuppliers-deploy/1.0"}

    with tempfile.TemporaryDirectory() as td:
        key=td+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=Path(key+".pub").read_text().strip()
        _,created=req_json(
            f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/",
            "POST",json.dumps({"key":pub,"name":"chatgpt-megasuppliers-install-"+os.environ.get("GITHUB_RUN_ID","manual")}).encode(),headers
        )
        kid=str(created["id"])
        try:
            connected=False
            for _ in range(18):
                p=ssh(key,"echo ssh_ok",check=False,timeout=25)
                if p.returncode==0:
                    connected=True; break
                time.sleep(5)
            if not connected:
                raise RuntimeError("temporary SSH unavailable")
            log("ssh=yes")

            # Read-only bootstrap test on an existing plugin before any write.
            bootstrap = f"""<?php
chdir({ROOT!r});
require_once {ROOT!r}.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null, new SystemConfig());
wa('shop');
$p=wa('shop')->getPlugin('nordenstock', true);
echo get_class($p),"\\n";
"""
            p=ssh(key,"cat >/tmp/ms_bootstrap_test.php && chown web:web /tmp/ms_bootstrap_test.php && su -s /bin/bash web -c 'php /tmp/ms_bootstrap_test.php'",stdin=bootstrap,check=True)
            log("bootstrap="+p.stdout.strip())

            subprocess.run([
                "scp","-i",key,"-o","StrictHostKeyChecking=no","-o","UserKnownHostsFile=/dev/null",
                pkg,"root@"+VM_IP+":/tmp/megasuppliers-1.0.1.zip"
            ],check=True)

            remote = f"""set -euo pipefail
ROOT={shlex.quote(ROOT)}
PLUGIN="$ROOT/wa-apps/shop/plugins/megasuppliers"
CONF="$ROOT/wa-config/apps/shop/plugins.php"
TS="$(date +%Y%m%d-%H%M%S)"
BACK="/home/web/backups/chatgpt-megasuppliers/$TS"
STAGE="/tmp/megasuppliers-stage-$TS"
mkdir -p "$BACK" "$STAGE"
cp -a "$CONF" "$BACK/plugins.php.before"
if [ -e "$PLUGIN" ]; then
  tar -C "$(dirname "$PLUGIN")" -czf "$BACK/megasuppliers.before.tgz" "$(basename "$PLUGIN")"
fi
unzip -q /tmp/megasuppliers-1.0.1.zip -d "$STAGE"
test -f "$STAGE/megasuppliers/lib/config/plugin.php"
find "$STAGE/megasuppliers" -name '*.php' -print0 | while IFS= read -r -d '' f; do php -l "$f" >/dev/null; done
rm -rf "$PLUGIN"
cp -a "$STAGE/megasuppliers" "$PLUGIN"
chown -R web:web "$PLUGIN"
find "$PLUGIN" -type d -exec chmod 755 {{}} +
find "$PLUGIN" -type f -exec chmod 644 {{}} +

cat >/tmp/ms_register.php <<'PHP'
<?php
$path = {ROOT!r}.'/wa-config/apps/shop/plugins.php';
$p = file_exists($path) ? include($path) : array();
if (!is_array($p)) $p = array();
$p['megasuppliers'] = true;
$data = "<?php\n\nreturn ".var_export($p, true).";\n//EOF";
if (file_put_contents($path, $data) === false) exit(2);
PHP
chown web:web /tmp/ms_register.php
su -s /bin/bash web -c 'php /tmp/ms_register.php'

cat >/tmp/ms_init.php <<'PHP'
<?php
$root = {ROOT!r};
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null, new SystemConfig());
wa('shop');
$p=wa('shop')->getPlugin('megasuppliers', true);
echo "class=".get_class($p)."\n";
echo "version=".$p->getVersion()."\n";
$m=new waModel();
$tables=array('shop_megasuppliers_supplier','shop_megasuppliers_product','shop_megasuppliers_import','shop_megasuppliers_meta');
foreach($tables as $t) {{
    $r=$m->query("SHOW TABLES LIKE s:table", array('table'=>$t))->fetch();
    echo "table_".$t."=".($r ? "yes" : "no")."\n";
}}
echo "suppliers=".(new shopMegasuppliersSupplierModel())->countAll()."\n";
$meta=(new shopMegasuppliersMetaModel())->getById('api_key');
echo "api_key_generated=".(($meta && strlen((string)$meta['value'])>=64) ? "yes" : "no")."\n";
PHP
chown web:web /tmp/ms_init.php
su -s /bin/bash web -c 'php /tmp/ms_init.php'

echo "config_enabled=$(php -r '$p=include "'$CONF'"; echo !empty($p["megasuppliers"]) ? "yes" : "no";')"
echo "backup=$BACK"
rm -rf "$STAGE" /tmp/ms_register.php /tmp/ms_init.php /tmp/ms_bootstrap_test.php /tmp/megasuppliers-1.0.1.zip
"""
            p=ssh(key,"bash -s",stdin=remote,check=True,timeout=180)
            log(p.stdout.strip())
            if p.stderr.strip():
                log("remote_stderr="+p.stderr.strip()[-1200:])

            # HTTP health checks. API GET is expected to be non-2xx (405) but must hit the plugin route.
            for url in ("https://profikompany.ru/","https://profikompany.ru/megasuppliers-api/"):
                try:
                    rq=urllib.request.Request(url,headers={"User-Agent":"megasuppliers-install-check/1.0"})
                    with urllib.request.urlopen(rq,timeout=30) as rr:
                        body=rr.read(1000).decode("utf-8","replace")
                        log(f"http {url} {rr.status} body={body[:160]!r}")
                except urllib.error.HTTPError as e:
                    body=e.read(1000).decode("utf-8","replace")
                    log(f"http {url} {e.code} body={body[:200]!r}")
            log("INSTALL_STATUS=SUCCESS")
        finally:
            try:
                req_json(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
                log("temp_key_removed=yes")
            except Exception as e:
                log("temp_key_removed=no "+repr(e))

    REPORT.write_text("\n".join(lines)+"\n",encoding="utf-8")

if __name__=="__main__":
    try:
        main()
    except Exception as e:
        REPORT.parent.mkdir(exist_ok=True)
        with REPORT.open("a",encoding="utf-8") as f:
            f.write("INSTALL_STATUS=FAILED\nERROR="+repr(e)+"\n")
        raise
