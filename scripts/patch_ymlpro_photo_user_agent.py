#!/usr/bin/env python3
"""Guarded live patch for a verified false-negative Garda Decor HEAD check.

HTTP 403 is caused by empty cURL User-Agent; browser-like HEAD returns image/jpeg 200.
No TLS weakening, URL rewriting, supplier import, or customer/product mutation.
"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute

REMOTE=r"""set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
TARGET="$ROOT/wa-apps/shop/plugins/univimport/lib/classes/shopUnivimportPluginUniversalPhotoSync.class.php"
test -f "$TARGET"
BACKUP_DIR=/home/web/vm-23f9aff9.na4u.ru/ymlpro-image-backups
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
BACKUP="$BACKUP_DIR/photo-ua-before-$(date -u +%Y%m%dT%H%M%S)-$$.php"
cp -p "$TARGET" "$BACKUP"
restore_on_error() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    cp -p "$BACKUP" "$TARGET"
    echo "GARDA_USER_AGENT_ROLLBACK=true"
  fi
}
trap restore_on_error EXIT
python3 - "$TARGET" <<'PY'
import os,sys,tempfile
from pathlib import Path
f=Path(sys.argv[1]);original=f.read_text()
old="CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2));"
new="CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2,\n              CURLOPT_USERAGENT=>'Mozilla/5.0 (compatible; catalog-photo-check/1.0)'));"
if original.count(new)==1 and old not in original:
    print("GARDA_USER_AGENT_PATCH=already_present",flush=True)
elif original.count(old)==1 and new not in original:
    updated=original.replace(old,new,1)
    st=f.stat()
    fd,name=tempfile.mkstemp(prefix='.photo-ua-',dir=str(f.parent))
    try:
        with os.fdopen(fd,'w') as out: out.write(updated)
        os.chmod(name,st.st_mode)
        os.chown(name,st.st_uid,st.st_gid)
        os.replace(name,str(f))
    finally:
        if os.path.exists(name):os.unlink(name)
    print("GARDA_USER_AGENT_PATCH=applied",flush=True)
else: raise RuntimeError("Unexpected live PHP testPhotos signature; unsafe to modify")
PY
php -l "$TARGET"
F="$(mktemp /tmp/garda-photo-verify-XXXXXX.php)"
trap 'rm -f "$F"; restore_on_error' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
chdir('/home/web/vm-23f9aff9.na4u.ru/www');
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
require_once 'wa-apps/shop/plugins/univimport/lib/classes/shopUnivimportPluginUniversalPhotoSync.class.php';
$cls=new ReflectionClass('shopUnivimportPluginUniversalPhotoSync');
$p=$cls->getMethod('profile');$p->setAccessible(true);
$plan=$cls->getMethod('plan');$plan->setAccessible(true);
$test=$cls->getMethod('testPhotos');$test->setAccessible(true);
$db=new waModel();
$config=$p->invoke(null,$db,202);
$changes=$plan->invoke(null,202,$config,$db);
$ok=(bool)$test->invoke(null,$changes['changes']);
echo 'GARDA_USER_AGENT_VERIFY='.json_encode(array('planned'=>$changes['changed'],'head_images_reachable'=>$ok))."\n";
if(!$ok || $changes['changed']<100)exit(2);
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
echo "GARDA_USER_AGENT_READY=true"
"""
result=execute(REMOTE)
for line in result.splitlines():
    if line.startswith("GARDA_USER_AGENT_"):print(line,flush=True)
if "GARDA_USER_AGENT_READY=true" not in result:
    raise RuntimeError("Garda URL validation patch not verified")
