#!/usr/bin/env python3
"""Read-only pilot of YML PRO image adapter."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/ymlpro-photo-dry-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
require_once '/home/web/vm-23f9aff9.na4u.ru/www/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
require_once 'wa-apps/shop/plugins/univimport/lib/classes/shopUnivimportPluginYmlproPhotoSync.class.php';
$items=shopUnivimportPluginYmlproPhotoSync::collect(231,5);
foreach($items as $v){
 echo 'YMLPHOTO_PREVIEW='.json_encode(array('id'=>$v['product_id'],'photos'=>count($v['photos']),
 'host'=>parse_url($v['photos'][0],PHP_URL_HOST),'native'=>$v['native_image']>0))."\n";
}
echo 'YMLPHOTO_COUNT='.count($items)."\n";
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
out=execute(REMOTE)
for l in out.splitlines():
 if l.startswith("YMLPHOTO_"):print(l,flush=True)
if "YMLPHOTO_COUNT=5" not in out:raise RuntimeError("Pilot candidates not valid")
