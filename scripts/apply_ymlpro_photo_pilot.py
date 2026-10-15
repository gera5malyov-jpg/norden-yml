#!/usr/bin/env python3
"""Authorized five-product pilot with private, reversible summaries backup."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute

REMOTE=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/ymlpro-photo-apply-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
require_once '/home/web/vm-23f9aff9.na4u.ru/www/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
require_once 'wa-apps/shop/plugins/univimport/lib/classes/shopUnivimportPluginYmlproPhotoSync.class.php';
$items=shopUnivimportPluginYmlproPhotoSync::collect(231,5);
$expected=array(1199339,1199328,1458758,1458759,1458760);
$actual=array_map(function($x){return (int)$x['product_id'];},$items);
if($actual!==$expected)throw new RuntimeException('Pilot candidate identities changed; refusing write');
$dir=wa()->getDataPath('plugins/univimport/rollback',false,'shop',true);
if(!is_dir($dir))waFiles::create($dir);
$backup=$dir.'/everprof-'.gmdate('Ymd-His').'-'.getmypid().'.json';
$result=shopUnivimportPluginYmlproPhotoSync::apply($items,$backup);
foreach($result as $x){
 $product=(new shopProductModel())->getById($x['id']);
 if($x['status']!=='updated' || substr_count((string)$product['summary'],'[extimg]')<2){
    throw new RuntimeException('Pilot incomplete at product '.(int)$x['id']);
 }
 echo 'YMLPHOTO_UPDATED='.json_encode($x,JSON_UNESCAPED_UNICODE)."\n";
}
echo 'YMLPHOTO_BACKUP=saved_in_protected_storage'."\n";
echo 'YMLPHOTO_PILOT=success'."\n";
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith("YMLPHOTO_"):print(line,flush=True)
if "YMLPHOTO_PILOT=success" not in out:raise RuntimeError("Pilot not verified")
