#!/usr/bin/env python3
"""Run one or more licensed YML PRO profiles with the generic photo companion.

UNIVERSAL_PROFILE_IDS="20,16"; UNIVERSAL_APPLY=0 (default dry-run).
Production wrapper deliberately keeps credentials and product descriptions private.
"""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute

# Optional automatic discovery: select every linked YML PRO profile in one shard.
if os.getenv("UNIVERSAL_SHARD") is not None:
    shard = int(os.environ["UNIVERSAL_SHARD"])
    count = int(os.getenv("UNIVERSAL_SHARD_COUNT", "8"))
    if count < 1 or count > 32 or shard < 0 or shard >= count:
        raise RuntimeError("Invalid automatic photo shard")
    discovery = r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/yml-photo-shards-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
require_once 'wa-apps/shop/plugins/univimport/lib/classes/shopUnivimportPluginUniversalPhotoSync.class.php';
$ids=array();
foreach(shopUnivimportPluginUniversalPhotoSync::profiles() as $p){
 if($p['linked']>0 && $p['id']%SHARD_COUNT===SHARD_NUMBER)$ids[]=$p['id'];
}
echo 'UNIVERSAL_DISCOVERED='.implode(',',$ids)."\\n";
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
""".replace("SHARD_COUNT", str(count)).replace("SHARD_NUMBER", str(shard))
    reply = execute(discovery)
    selected = [line.split("=", 1)[1] for line in reply.splitlines()
                if line.startswith("UNIVERSAL_DISCOVERED=")]
    if not selected:
        raise RuntimeError("No profile discovery result")
    os.environ["UNIVERSAL_PROFILE_IDS"] = selected[-1]
raw=os.getenv("UNIVERSAL_PROFILE_IDS","20,16")
profile_ids=[int(x.strip()) for x in raw.split(",") if x.strip()]
if (not profile_ids and os.getenv("UNIVERSAL_SHARD") is None) or len(profile_ids)>12 or any(x<1 or x>1000000 for x in profile_ids):
    raise RuntimeError("Invalid profile-id selection")
apply=os.getenv("UNIVERSAL_APPLY","0")=="1"
strict=os.getenv("UNIVERSAL_STRICT","1")=="1"
success=0
failed=0
for pid in profile_ids:
    remote=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/universal-yml-photo-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
require_once 'wa-apps/shop/plugins/univimport/lib/classes/shopUnivimportPluginUniversalPhotoSync.class.php';
try {
 $result=shopUnivimportPluginUniversalPhotoSync::run(PROFILE_NUMBER,SHOULD_APPLY);
 echo 'UNIVERSAL_RESULT='.json_encode($result,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
} catch(Throwable $e) {
 echo 'UNIVERSAL_PROFILE_ERROR='.json_encode(array('profile_id'=>PROFILE_NUMBER,'class'=>get_class($e),
 'reason'=>substr($e->getMessage(),0,160)),JSON_UNESCAPED_UNICODE)."\n";
 exit(2);
}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
""".replace("PROFILE_NUMBER",str(pid)).replace("SHOULD_APPLY","true" if apply else "false")
    try:
        out=execute(remote)
        for line in out.splitlines():
            if line.startswith(("UNIVERSAL_RESULT=","UNIVERSAL_PROFILE_ERROR=")):
                print(line,flush=True)
        if "UNIVERSAL_RESULT=" not in out:raise RuntimeError("No per-profile result")
        success+=1
    except Exception as exc:
        failed+=1
        print("UNIVERSAL_RUN_FAILED=profile_id:{};category:{}".format(pid,type(exc).__name__),flush=True)
print("UNIVERSAL_SUMMARY=success:{};failed:{};apply:{}".format(success,failed,int(apply)),flush=True)
if strict and failed:raise RuntimeError("One or more profiles did not pass preflight")
