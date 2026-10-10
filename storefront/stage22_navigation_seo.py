#!/usr/bin/env python3
"""Stage22: polish SEO and remove duplicate CMS headings for new footer pages."""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r'''set -eu
root=/home/web/vm-23f9aff9.na4u.ru
backup=$(mktemp -d "$root/storefront-backups/stage22-navigation-seo-XXXXXXXX")
chmod 700 "$backup"
index="$root/www/wa-data/public/site/themes/pureMegapolis42/index.html"
cp "$index" "$backup/index.html"
chmod 600 "$backup/index.html"
tmp=$(mktemp /tmp/stage22-seo-XXXXXX.php)
jsonbackup=$(mktemp /tmp/stage22-cms-XXXXXX.json)
chown web:web "$jsonbackup"
chmod 600 "$jsonbackup"
trap 'rm -f "$tmp" "$jsonbackup"' EXIT
cat > "$tmp" <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$model=new shopPageModel();
$pages=array(
21=>array('slug'=>'proizvoditeli/','title'=>'Производители мебели и предметов интерьера | Мегаполис','name'=>'Производители'),
22=>array('slug'=>'garantiya/','title'=>'Гарантия и возврат мебели | Мегаполис','name'=>'Гарантия'),
23=>array('slug'=>'vozmozhnosti/','title'=>'Возможности интернет-магазина мебели | Мегаполис','name'=>'Возможности')
);
$before=array();
foreach($pages as $id=>$page){
 $r=$model->getById($id);
 if(!$r || $r['domain']!=='profikompany.ru' || $r['url']!==$page['slug'] || $r['name']!==$page['name'])throw new Exception('Page identity differs '.$id);
 if(strpos($r['content'],'<h1>')!==0)throw new Exception('Content title has already changed '.$id);
 $before[$id]=$r['content'];
}
$bp=getenv('STAGE22_DB_BACKUP');
if(!$bp || file_put_contents($bp,json_encode($before,JSON_UNESCAPED_UNICODE))===false)throw new Exception('Cannot back up CMS text');
$path=$root.'/wa-data/public/site/themes/pureMegapolis42/index.html';
$old=file_get_contents($path);
if($old===false)throw new Exception('Cannot read SEO theme');
$anchor=<<<'TPL'
      {if $_seo_info_url == '/privacy-policy/'}
TPL;
$inject=<<<'TPL'
      {if $_seo_info_url == '/proizvoditeli/'}
        {$_info_title = 'Производители мебели и предметов интерьера | Мегаполис'}
        {$_info_description = 'Производители и торговые марки из каталога Мегаполис. Алфавитный справочник со ссылками на поиск мебели по производителю.'}
      {/if}
      {if $_seo_info_url == '/garantiya/'}
        {$_info_title = 'Гарантия и возврат мебели | Мегаполис'}
        {$_info_description = 'Гарантийные обращения, возврат мебели, права покупателей и способы связи с интернет-магазином Мегаполис.'}
      {/if}
      {if $_seo_info_url == '/vozmozhnosti/'}
        {$_info_title = 'Возможности интернет-магазина мебели | Мегаполис'}
        {$_info_description = 'Подбор мебели, просмотр характеристик, онлайн-заказ, условия доставки и оплаты, консультации магазина Мегаполис.'}
      {/if}

TPL;
if(substr_count($old,$anchor)!==1 || strpos($old,"Производители мебели и предметов интерьера | Мегаполис")!==false)throw new Exception('SEO source changed');
$new=str_replace($anchor,$inject.$anchor,$old);
$cAnchor=<<<'TPL'
          {if $_seo_url == '/privacy-policy/'}
            <link rel="canonical" href="{$wa->currentUrl(true, true)|escape}" />
          {/if}
TPL;
$cInject=<<<'TPL'
          {if $_seo_url == '/proizvoditeli/' || $_seo_url == '/garantiya/' || $_seo_url == '/vozmozhnosti/'}
            <link rel="canonical" href="{$wa->currentUrl(true, true)|escape}" />
          {/if}

TPL;
if(substr_count($new,$cAnchor)!==1)throw new Exception('Canonical anchor changed');
$new=str_replace($cAnchor,$cAnchor."\n".$cInject,$new);
$updated=array();$themeChanged=false;
try{
 foreach($pages as $id=>$page){
  $content=preg_replace('~^<h1>[^<]+</h1>\s*~u','',$before[$id],1,$count);
  if($count!==1 || strlen($content)<500)throw new Exception('CMS heading rewrite failed '.$id);
  $model->updateById($id,array('content'=>$content));
  $updated[]=$id;
 }
 $p=tempnam(dirname($path),'.stage22-seo-');
 if(!$p || file_put_contents($p,$new)===false)throw new Exception('Cannot write SEO template');
 $st=stat($path);chmod($p,$st['mode']&0777);
 rename($p,$path);$themeChanged=true;
 $tests=array();
 foreach($pages as $page){
  $f='/tmp/stage22-check-'.getmypid();$out=array();$rc=0;
  $url='https://profikompany.ru/'.$page['slug'].'?stage22_check='.getmypid();
  exec('curl -k -sSL --max-time 30 -o '.escapeshellarg($f)." -w '%{http_code}' ".escapeshellarg($url),$out,$rc);
  $html=@file_get_contents($f);@unlink($f);
  $status=trim(implode('',$out));
  $expected='<title>'.$page['title'].'</title>';
  $ok=$rc===0 && $status==='200' && $html!==false && strpos($html,$expected)!==false &&
       substr_count($html,'rel="canonical"')===1;
  $tests[]=array('slug'=>$page['slug'],'http'=>$status,'seo_ok'=>$ok);
  if(!$ok)throw new Exception('SEO page smoke test failed: '.$page['slug'].' HTTP '.$status);
 }
 echo 'STAGE22_APPLY='.json_encode(array('success'=>true,'pages'=>$tests,'cms_updated'=>$updated),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
}catch(Throwable $e){
 foreach($updated as $id)$model->updateById($id,array('content'=>$before[$id]));
 if($themeChanged)file_put_contents($path,$old);
 echo 'STAGE22_ROLLBACK='.json_encode(array('reason'=>$e->getMessage(),'ids_restored'=>$updated,'theme_restored'=>$themeChanged),JSON_UNESCAPED_UNICODE)."\n";
 throw $e;
}
?>
PHP
chmod 644 "$tmp"
set +e
su -s /bin/bash web -c "STAGE22_DB_BACKUP='$jsonbackup' php -d display_errors=1 -d log_errors=0 '$tmp'"
status=$?
set -e
if test -s "$jsonbackup"; then cp "$jsonbackup" "$backup/cms_pages.json"; chmod 600 "$backup/cms_pages.json"; fi
printf 'STAGE22_BACKUP=%s\n' "$backup"
exit "$status"
'''
def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('Missing NETANGELS_API_KEY')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
   urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels token unavailable')
 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage22-seo-') as d:
  ssh=d+'/id_ed25519'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
  pub=Path(ssh+'.pub').read_text().strip()
  payload=json.dumps({'key':pub,'name':'stage22-seo-'+os.getenv('GITHUB_RUN_ID','manual')}).encode()
  resp=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',payload,headers)
  kid=resp.get('id')
  if not kid:raise RuntimeError('Could not register temporary SSH key')
  try:
   p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
       '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
       input=REMOTE,text=True,capture_output=True,timeout=170)
   for line in p.stdout.splitlines():
     if line.startswith('STAGE22_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('Title deploy failed stdout='+p.stdout[-600:]+' stderr='+p.stderr[-600:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: temporary SSH key cleanup needs review')
if __name__=='__main__':run()
