#!/usr/bin/env python3
"""Normalize old Mebelik HTTP picture links in profile 28 summaries only; snapshot first."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
remote=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/mbk-legacy-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$rows=$db->query('SELECT DISTINCT p.id,p.summary, '.
 '(SELECT COUNT(*) FROM shop_yml_products x WHERE x.shop_id=p.id AND x.profile_id<>28) AS shared '.
 'FROM shop_yml_products m JOIN shop_product p ON p.id=m.shop_id '.
 'WHERE m.profile_id=28 AND p.type_id=31')->fetchAll();
$changes=array();$unchanged=0;$shared=0;$noExt=0;
$pattern='~(\[extimg\]\s*)(https?://(?:www[.])?mebelik[.]ru)([^\s<\[\]]*)(\s*\[/extimg\])~iu';
foreach($rows as $r) {
 $old=(string)$r['summary'];
 if(stripos($old,'[extimg]')===false){$noExt++;continue;}
 if((int)$r['shared']>0){$shared++;continue;}
 $new=preg_replace_callback($pattern,function($m){
  return $m[1].'https://mebelik.ru'.$m[3].$m[4];
 },$old);
 if($new===null)throw new RuntimeException('Mebelik legacy regex error');
 if($new===$old){$unchanged++;continue;}
 $changes[]=array('id'=>(int)$r['id'],'old'=>$old,'new'=>$new);
}
$report=array('mapped'=>count($rows),'unchanged_with_photos'=>$unchanged,
 'without_extimg'=>$noExt,'skipped_shared'=>$shared,'to_update'=>count($changes));
echo 'MBK_LEGACY_PREVIEW='.json_encode($report,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
if(count($rows)<900 || count($rows)>1200 || $shared>0 || count($changes)>281)
 throw new RuntimeException('Mebelik legacy scope unexpected, no edits');
if(!$changes) {echo 'MBK_LEGACY_APPLY={"updated":0,"status":"already_current"}'."\n";exit(0);}
$first=$changes[0]['new'];
preg_match('~\[extimg\]\s*(https?://[^\s<]+)~i',$first,$m);
if(empty($m[1]))throw new RuntimeException('No sample image found');
$ch=curl_init($m[1]);
curl_setopt_array($ch,array(CURLOPT_NOBODY=>true,CURLOPT_RETURNTRANSFER=>true,
 CURLOPT_CONNECTTIMEOUT=>7,CURLOPT_TIMEOUT=>15,CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2,
 CURLOPT_CAINFO=>'/home/web/vm-23f9aff9.na4u.ru/www/wa-config/mebelik-globalsign-root-r46.pem'));
curl_exec($ch);$status=(int)curl_getinfo($ch,CURLINFO_HTTP_CODE);$type=(string)curl_getinfo($ch,CURLINFO_CONTENT_TYPE);curl_close($ch);
echo 'MBK_LEGACY_TEST='.json_encode(array('id'=>$changes[0]['id'],'sample_host'=>parse_url($m[1],PHP_URL_HOST),'status'=>$status,'mime'=>$type),JSON_UNESCAPED_UNICODE)."\n";
if(($status!==200 && $status!==206) || stripos($type,'image/')!==0)throw new RuntimeException('New Mebelik sample image unreachable');
$backup_dir=wa()->getDataPath('plugins/univimport/rollback',false,'shop',true);
if(!is_dir($backup_dir))waFiles::create($backup_dir);
$old=array();foreach($changes as $v)$old[]=array('id'=>$v['id'],'summary'=>$v['old']);
$encoded=json_encode(array('profile_id'=>28,'created_at'=>gmdate('c'),'reason'=>'legacy-mbk-url-normalize','before'=>$old),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
if(!$encoded)throw new RuntimeException('Backup JSON failed');
$compressed=gzencode($encoded,6);
if(!$compressed)throw new RuntimeException('Backup gzip failed');
$backup=$backup_dir.'/mebelik-legacy-photo-'.gmdate('Ymd-His').'-'.getmypid().'.json.gz';
$tmp=$backup.'.pending';
if(file_put_contents($tmp,$compressed,LOCK_EX)!==strlen($compressed))throw new RuntimeException('Backup write failed');
@chmod($tmp,0600);
if(!rename($tmp,$backup))throw new RuntimeException('Backup finalization failed');
$model=new shopProductModel();$updated=0;$concurrent=0;
foreach($changes as $v){
 $current=$model->getById($v['id']);
 if(!$current || (string)$current['summary']!==$v['old']){$concurrent++;continue;}
 $model->updateById($v['id'],array('summary'=>$v['new']));
 $updated++;
}
echo 'MBK_LEGACY_APPLY='.json_encode(array('updated'=>$updated,'concurrent_skipped'=>$concurrent,
 'status'=>($concurrent?'partial':'apply'),'backup_created'=>is_file($backup)),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
if($concurrent || $updated!==count($changes))throw new RuntimeException('Legacy apply incomplete');
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
out=execute(remote)
for line in out.splitlines():
 if line.startswith(('MBK_LEGACY_PREVIEW=','MBK_LEGACY_APPLY=')): print(line,flush=True)
if 'MBK_LEGACY_APPLY=' not in out: raise RuntimeError('Mebelik legacy result missing')
print('MBK_LEGACY_FINISHED=success',flush=True)
