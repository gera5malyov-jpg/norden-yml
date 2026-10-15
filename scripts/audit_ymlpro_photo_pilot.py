#!/usr/bin/env python3
"""Read-only pilot selection: no product data or secrets are changed."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute

REMOTE=r"""set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/yml-photo-pilot-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
$root='/home/web/vm-23f9aff9.na4u.ru/www';chdir($root);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();$out=array();
foreach($db->query("SELECT id,name,config FROM shop_yml_profiles")->fetchAll() as $p){
 $c=json_decode($p['config'],true);
 if(!is_array($c))continue;
 $id=(int)$p['id'];
 $agg=$db->query("SELECT COUNT(*) cnt,SUM(p.id IS NOT NULL AND p.summary LIKE '%[extimg]%') extimg,SUM(p.id IS NOT NULL AND p.image_id>0) native_img,SUM(p.id IS NOT NULL AND p.status=1) published FROM shop_yml_products yp LEFT JOIN shop_product p ON p.id=yp.shop_id WHERE yp.profile_id=".$id)->fetch();
 $u=isset($c['url'])?(string)$c['url']:'';
 $out[]=array('id'=>$id,'name'=>mb_substr((string)$p['name'],0,65),'host'=>$u?parse_url($u,PHP_URL_HOST):'','https'=>strpos($u,'https://')===0,
 'source_type'=>isset($c['source_type'])?$c['source_type']:null,'images'=>isset($c['images'])?$c['images']:null,'max_img'=>isset($c['max_img'])?$c['max_img']:null,
 'login'=>!empty($c['http_login'])||!empty($c['http_auth']),
 'mapped'=>(int)$agg['cnt'],'extimg'=>(int)$agg['extimg'],'native'=>(int)$agg['native_img'],'published'=>(int)$agg['published']);
}
usort($out,function($a,$b){return $b['mapped']-$a['mapped'];});
echo 'PILOT_PROFILES='.json_encode(array_slice($out,0,32),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith("PILOT_"):print(line)
if "PILOT_PROFILES=" not in out:raise RuntimeError("Missing pilot result")
