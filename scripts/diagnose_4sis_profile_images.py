#!/usr/bin/env python3
"""Read-only, secret-safe diagnostics for YML PRO profile 4sis only."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/4sis-photo-diagnose-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$r=$db->query("SELECT id,name,config FROM shop_yml_profiles WHERE id=19")->fetch();
if(!$r || trim((string)$r['name'])!=='4sis')throw new RuntimeException('4sis profile mismatch');
$c=json_decode($r['config'],true);if(!is_array($c))throw new RuntimeException('Invalid configuration');
$url=isset($c['url'])?trim((string)$c['url']):'';
$parts=$url?parse_url($url):array();
$out=array('id'=>19,'profile'=>'4sis',
'source_type'=>isset($c['source_type'])?$c['source_type']:null,
'scheme'=>isset($parts['scheme'])?$parts['scheme']:'',
'host'=>isset($parts['host'])?$parts['host']:'',
'has_url'=>$url!=='',
'url_valid'=>is_array($parts),
'embedded_user'=>isset($parts['user']),
'embedded_password'=>isset($parts['pass']),
'has_custom_http_login'=>!empty($c['http_login']),
'has_custom_http_auth'=>!empty($c['http_auth']),
'image_mode'=>isset($c['images'])?$c['images']:null,
'url_has_query'=>!empty($parts['query']),
'url_has_fragment'=>!empty($parts['fragment']));
$fields=array_keys($c);$out['image_keys']=array_values(array_filter($fields,function($k){return preg_match('/image|photo|picture|source|file|url/i',$k);}));
echo 'FOURSIS_SOURCE='.json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES).PHP_EOL;
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
out=execute(REMOTE)
for x in out.splitlines():
 if x.startswith("FOURSIS_SOURCE="): print(x,flush=True)
if "FOURSIS_SOURCE=" not in out: raise RuntimeError("Diagnostic result missing")
