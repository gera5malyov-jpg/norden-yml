#!/usr/bin/env python3
"""Whole-profile Deephouse photo sync; preview by default, apply only with explicit environment flag."""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute

APPLY = os.environ.get("DEEPHOUSE_FULL_APPLY","0")=="1"

REMOTE=r"""set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
cd "$ROOT"
F="$(mktemp /tmp/deephouse-extimg-sync-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat > "$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
require_once 'wa-apps/shop/plugins/univimport/lib/classes/shopUnivimportPluginDeephouseSync.class.php';
try { $plan=shopUnivimportPluginDeephouseSync::prepare(); } catch (Throwable $e) { echo 'DEEPHOUSE_SYNC_ERROR='.get_class($e).' '.substr($e->getMessage(),0,200)."\n"; exit(4); }
$sample=array();
foreach(array_slice($plan['changes'],0,8) as $item){
 $sample[]=array('id'=>$item['id'],'photos'=>$item['photos'],
  'old_bytes'=>strlen($item['old']),'new_bytes'=>strlen($item['new']));
}
echo 'DEEPHOUSE_SYNC_PREFLIGHT='.json_encode(array(
 'mapped'=>$plan['mapped'],'offers'=>$plan['feed_offers'],
 'updated_needed'=>count($plan['changes']),'already_current'=>$plan['unchanged'],
 'not_in_feed'=>$plan['not_in_feed'],'unmapped_source_offers'=>$plan['unmapped_offers_skipped'],'supplier_image_links'=>$plan['photos'],
 'examples'=>$sample),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
if ('APPLY_FLAG'==='1') {
 $result=shopUnivimportPluginDeephouseSync::apply($plan);
 echo 'DEEPHOUSE_SYNC_APPLY='.json_encode($result,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
 if (!$result['backup_exists'] || $result['updated']<900) throw new RuntimeException('Insufficient updates or missing rollback');
} else {
 echo "DEEPHOUSE_SYNC_DRY_RUN=success\n";
}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=1 '$F'"
"""
out=execute(REMOTE.replace("APPLY_FLAG","1" if APPLY else "0"))
for line in out.splitlines():
 if line.startswith("DEEPHOUSE_SYNC_"):print(line,flush=True)
expected="DEEPHOUSE_SYNC_APPLY=" if APPLY else "DEEPHOUSE_SYNC_DRY_RUN=success"
if expected not in out:raise RuntimeError("Whole-profile photo sync verification missing")
