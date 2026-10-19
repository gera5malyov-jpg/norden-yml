#!/usr/bin/env python3
"""Repair existing Mebelik multiline [extimg] legacy URLs only, preserving block formatting."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute

remote=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/mbk-legacy-fix-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$rows=$db->query('SELECT DISTINCT p.id,p.summary,p.image_id, '.
 '(SELECT COUNT(*) FROM shop_yml_products x WHERE x.shop_id=p.id AND x.profile_id<>28) AS shared '.
 'FROM shop_yml_products m JOIN shop_product p ON p.id=m.shop_id '.
 'WHERE m.profile_id=28 AND p.type_id=31')->fetchAll();
$changes=array();$same=0;$without=0;$shared=0;
$block='~(\[extimg\])([\s\S]*?)(\[/extimg\])~iu';
$origin='~https?://(?:www[.])?mebelik[.]ru(?=/|$)~i';
foreach($rows as $r){
 $old=(string)$r['summary'];
 if(stripos($old,'[extimg]')===false){$without++;continue;}
 if((int)$r['shared']>0){$shared++;continue;}
 $new=preg_replace_callback($block,function($m)use($origin){
  $inside=preg_replace($origin,'https://mebelik.ru',$m[2]);
  if($inside===null)throw new RuntimeException('Mebelik URL normalization regex failed');
  return $m[1].$inside.$m[3];
 },$old);
 if($new===null)throw new RuntimeException('Mebelik extimg block regex failed');
 if($new===$old){$same++;continue;}
 $changes[]=array('id'=>(int)$r['id'],'old'=>$old,'new'=>$new);
}
$stats=array('mapped'=>count($rows),'without_extimg'=>$without,
 'already_current'=>$same,'shared_skipped'=>$shared,'to_update'=>count($changes));
echo 'MBK_MULTI_PREVIEW='.json_encode($stats,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
if(count($rows)<900 || count($rows)>1200 || $shared>0 || count($changes)>281 || count($changes)<1)
 throw new RuntimeException('Mebelik scope outside safe expected range');
$checked=0;$reachable=0;$sample=array();
foreach($changes as $v) {
 preg_match_all('~https://mebelik[.]ru/[^\s<]+~i',$v['new'],$ms);
 foreach($ms[0] as $url){
  if($checked>=18)break 2;
  $ch=curl_init($url);
  curl_setopt_array($ch,array(CURLOPT_NOBODY=>true,CURLOPT_RETURNTRANSFER=>true,
    CURLOPT_CONNECTTIMEOUT=>6,CURLOPT_TIMEOUT=>12,CURLOPT_FOLLOWLOCATION=>false,
    CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2,
    CURLOPT_CAINFO=>'/home/web/vm-23f9aff9.na4u.ru/www/wa-config/mebelik-globalsign-root-r46.pem'));
  curl_exec($ch);
  $status=(int)curl_getinfo($ch,CURLINFO_HTTP_CODE);
  $mime=(string)curl_getinfo($ch,CURLINFO_CONTENT_TYPE);
  curl_close($ch);
  $checked++;
  if(($status===200||$status===206)&&stripos($mime,'image/')===0)$reachable++;
  if(count($sample)<4)$sample[]=array('id'=>$v['id'],'status'=>$status,'mime'=>$mime);
 }
}
echo 'MBK_MULTI_REACHABILITY='.json_encode(array('checked'=>$checked,'reachable'=>$reachable,'samples'=>$sample),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
if(!$checked || !$reachable)throw new RuntimeException('No reachable sampled Mebelik photos, refusing bulk update');
$dir=wa()->getDataPath('plugins/univimport/rollback',false,'shop',true);
if(!is_dir($dir))waFiles::create($dir);
$before=array();foreach($changes as $v)$before[]=array('id'=>$v['id'],'summary'=>$v['old']);
$data=json_encode(array('profile_id'=>28,'reason'=>'legacy-multiline-https','created_at'=>gmdate('c'),'before'=>$before),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
if(!$data)throw new RuntimeException('Backup encode failed');
$compressed=gzencode($data,6);
if(!$compressed)throw new RuntimeException('Backup gzip failed');
$backup=$dir.'/mebelik-multiline-'.gmdate('Ymd-His').'-'.getmypid().'.json.gz';
$tmp=$backup.'.pending';
if(file_put_contents($tmp,$compressed,LOCK_EX)!==strlen($compressed))throw new RuntimeException('Backup write failed');
@chmod($tmp,0600);
if(!rename($tmp,$backup))throw new RuntimeException('Backup finalization failed');
$model=new shopProductModel();$updated=0;$skipped=0;
foreach($changes as $v){
 $cur=$model->getById($v['id']);
 if(!$cur || (string)$cur['summary']!==$v['old']){$skipped++;continue;}
 $model->updateById($v['id'],array('summary'=>$v['new']));
 $updated++;
}
echo 'MBK_MULTI_APPLY='.json_encode(array('updated'=>$updated,'concurrent_skipped'=>$skipped,'backup_created'=>is_file($backup)),JSON_UNESCAPED_UNICODE)."\n";
if($skipped || $updated!==count($changes))throw new RuntimeException('Incomplete concurrent Mebelik update');
$left=0;
$all=$db->query('SELECT p.summary FROM shop_yml_products m JOIN shop_product p ON p.id=m.shop_id WHERE m.profile_id=28 AND p.type_id=31')->fetchAll();
foreach($all as $r){
 preg_match_all($block,(string)$r['summary'],$blocks);
 foreach($blocks[2] as $txt){if(preg_match('~http://(?:www[.])?mebelik[.]ru|https://www[.]mebelik[.]ru~i',$txt)){$left++;break;}}
}
echo 'MBK_MULTI_POST='.json_encode(array('legacy_blocks_remaining'=>$left,'mapped'=>count($all),'newly_updated'=>$updated),JSON_UNESCAPED_UNICODE)."\n";
if($left)throw new RuntimeException('Legacy Mebelik urls remain after normalization');
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
out=execute(remote)
for line in out.splitlines():
 if line.startswith(('MBK_MULTI_PREVIEW=','MBK_MULTI_REACHABILITY=','MBK_MULTI_APPLY=','MBK_MULTI_POST=')):
  print(line,flush=True)
if 'MBK_MULTI_POST=' not in out:raise RuntimeError('Mebelik legacy post-check not confirmed')
print('MBK_MULTI_FINISHED=success',flush=True)
