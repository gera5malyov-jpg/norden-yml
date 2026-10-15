#!/usr/bin/env python3
"""Read-only, grouped metadata audit of all YML PRO profiles and image modes."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "storefront"))
from stage52_disable_callback import execute
REMOTE = r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/yml-profile-universal-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat > "$F" <<'PHP'
<?php
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$profiles=$db->query("SELECT id,name,config FROM shop_yml_profiles")->fetchAll();
$rows=array();
foreach($profiles as $p) {
 $c=json_decode($p['config'],true);
 if(!is_array($c))continue;
 $url=isset($c['url'])?trim((string)$c['url']):'';
 $parts=$url?parse_url($url):array();
 $id=(int)$p['id'];
 $stats=$db->query("SELECT COUNT(*) mapped, COUNT(DISTINCT yp.shop_id) unique_ids,
 SUM(p.id IS NULL) missing, SUM(p.image_id>0) local_photos,
 SUM(p.summary LIKE '%[extimg]%') external_photos
 FROM shop_yml_products yp LEFT JOIN shop_product p ON p.id=yp.shop_id WHERE yp.profile_id=".$id)->fetch();
 $rows[]=array('id'=>$id,'name'=>mb_substr((string)$p['name'],0,52),
 'scheme'=>is_array($parts)&&isset($parts['scheme'])?$parts['scheme']:'',
 'host'=>is_array($parts)&&isset($parts['host'])?$parts['host']:'',
 'has_url'=>!empty($url),'source_type'=>isset($c['source_type'])?$c['source_type']:null,
 'image_mode'=>isset($c['images'])?$c['images']:null,'max_img'=>isset($c['max_img'])?$c['max_img']:null,
 'authenticated'=>!empty($c['http_login'])||!empty($c['http_auth']),
 'mapped'=>(int)$stats['mapped'],'unique'=>(int)$stats['unique_ids'],'local'=>(int)$stats['local_photos'],
 'extimg'=>(int)$stats['external_photos'],'missing'=>(int)$stats['missing']);
}
usort($rows,function($a,$b){return $b['mapped']-$a['mapped'];});
$group=array();
foreach($rows as $r) {
 $key=($r['has_url']?'configured':'blank').' / '.$r['scheme'].' / mode '.$r['image_mode'];
 if(!isset($group[$key]))$group[$key]=array('count'=>0,'mapped'=>0,'extimg'=>0,'local'=>0);
 $group[$key]['count']++;$group[$key]['mapped']+=$r['mapped'];
 $group[$key]['extimg']+=$r['extimg'];$group[$key]['local']+=$r['local'];
}
echo 'UNIVERSAL_GROUPS='.json_encode($group,JSON_UNESCAPED_UNICODE)."\n";
foreach($rows as $r) echo 'UNIVERSAL_PROFILE='.json_encode($r,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith("UNIVERSAL_"):print(line,flush=True)
if "UNIVERSAL_GROUPS=" not in out:raise RuntimeError("Profile metadata report incomplete")
