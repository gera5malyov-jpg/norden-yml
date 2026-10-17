#!/usr/bin/env python3
"""Read-only: identify exact external image source for Avalon table 0108D."""
from stage52_disable_callback import execute

REMOTE = r"""set -eu
f=$(mktemp /tmp/avalon-0108d-audit-XXXXXX.php)
trap 'rm -f "$f"' EXIT
cat >"$f" <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();
$rows=$m->query("SELECT id,name,url,summary,description,image_id,price,count,status,edit_datetime FROM shop_product WHERE url='stol-stol-0108d' LIMIT 3")->fetchAll();
$out=array('matching_products'=>count($rows),'products'=>array());
foreach($rows as $row){
 $summary=(string)$row['summary'];
 $all=array();
 preg_match_all('~https?://[^\s<\]\[\\"\']+~i',$summary,$matches);
 foreach($matches[0] as $url){$all[]=html_entity_decode($url,ENT_QUOTES,'UTF-8');}
 $orig='https://www.avalonmebel.ru/upload/iblock/4cc/4cc538644a80454955d86c5677ccad55.jpg';
 $replacement='https://www.avalonmebel.ru/upload/iblock/5df/5dfb8d4cc4ae92edb5af75349bcae565.jpg';
 $out['products'][]=array('id'=>$row['id'],'name'=>$row['name'],'url'=>$row['url'],
 'price'=>$row['price'],'count'=>$row['count'],'status'=>$row['status'],
 'image_id'=>$row['image_id'],'edit_datetime'=>$row['edit_datetime'],
 'summary_bytes'=>strlen($summary),'summary_old_count'=>substr_count($summary,$orig),
 'summary_new_count'=>substr_count($summary,$replacement),
 'description_old_count'=>substr_count((string)$row['description'],$orig),
 'summary_has_extimg'=>strpos($summary,'[extimg]')!==false,
 'summary_sha256'=>hash('sha256',$summary),'external_urls'=>array_slice($all,0,14),
 'main_db_images'=>(int)$m->query('SELECT COUNT(*) FROM shop_product_images WHERE product_id='.(int)$row['id'])->fetchField()
 );
}
echo 'AVALON_0108D_AUDIT='.json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
PHP
chmod 644 "$f"
su -s /bin/bash web -c "php '$f'"
"""
if __name__ == '__main__':
    result = execute(REMOTE)
    if 'AVALON_0108D_AUDIT=' not in result:
        raise RuntimeError('Audit marker missing')
