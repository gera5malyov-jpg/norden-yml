#!/usr/bin/env python3
"""Create brand directory, guarantees and capabilities; simplify only active footer."""
import os,json,base64,tempfile,urllib.parse,subprocess
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json

REMOTE=r"""set -eu
root=/home/web/vm-23f9aff9.na4u.ru
backup=$(mktemp -d "$root/storefront-backups/stage21-pages-footer-XXXXXXXX")
chmod 700 "$backup"
footer="$root/www/wa-data/public/site/themes/pureMegapolis42/layouts/layout.footer.html"
cp "$footer" "$backup/layout.footer.html"
chmod 600 "$backup/layout.footer.html"
tmp=$(mktemp /tmp/stage21-XXXXXX.php)
trap 'rm -f "$tmp"' EXIT
cat > "$tmp" <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$pageModel=new shopPageModel();
$db=new waModel();
$footer=$root.'/wa-data/public/site/themes/pureMegapolis42/layouts/layout.footer.html';
$prior=file_get_contents($footer);
if($prior===false)throw new Exception('Could not read theme footer');
$existing=$db->query("SELECT id,url FROM shop_page WHERE domain='profikompany.ru' AND url IN ('proizvoditeli/','garantiya/','vozmozhnosti/')")->fetchAll();
if($existing)throw new Exception('Target page already exists, no changes');

$records=$db->query("SELECT v.value,COUNT(DISTINCT p.id) AS qty FROM shop_feature_values_varchar v JOIN shop_product_features f ON f.feature_value_id=v.id AND f.feature_id=9 JOIN shop_product p ON p.id=f.product_id AND p.status=1 WHERE v.feature_id=9 GROUP BY v.value ORDER BY v.value")->fetchAll();
$groups=array();$count=0;
foreach($records as $r){
 $name=trim((string)$r['value']);
 if(mb_strlen($name,'UTF-8')<2 || mb_strlen($name,'UTF-8')>80 || !preg_match('/\p{L}/u',$name))continue;
 $letter=mb_strtoupper(mb_substr($name,0,1,'UTF-8'),'UTF-8');
 if(!preg_match('/\p{L}/u',$letter))$letter='#';
 if(!isset($groups[$letter]))$groups[$letter]=array();
 $groups[$letter][]=$name;$count++;
}
if($count<150)throw new Exception('Published manufacturer feature list unexpectedly small');
ksort($groups,SORT_NATURAL|SORT_FLAG_CASE);
$brandPage='<h1>Производители мебели и предметов интерьера</h1><p>В каталоге «Мегаполис Маркет» представлены товары разных производителей и торговых марок. Нажмите на название производителя, чтобы открыть результаты поиска товаров его марки. Цены и наличие уточняйте в карточке выбранного товара.</p><p>В справочнике '.(int)$count.' производителей, указанных в характеристиках опубликованных товаров.</p>';
foreach($groups as $letter=>$names){
 $brandPage.='<details class="manufacturer-section"><summary style="cursor:pointer;padding:9px 0;font-weight:700;font-size:1.1em">'.htmlspecialchars($letter,ENT_QUOTES,'UTF-8').' ('.count($names).')</summary><ul style="columns:3;column-width:185px;line-height:1.9">';
 foreach($names as $name){
  $brandPage.='<li style="break-inside:avoid"><a href="/search/?query='.rawurlencode($name).'">'.htmlspecialchars($name,ENT_QUOTES,'UTF-8').'</a></li>';
 }
 $brandPage.='</ul></details>';
}
$pages=array(
 'proizvoditeli/'=>array('name'=>'Производители','title'=>'Производители мебели и предметов интерьера | Мегаполис','content'=>$brandPage),
 'garantiya/'=>array('name'=>'Гарантия','title'=>'Гарантия и возврат мебели | Мегаполис','content'=>base64_decode('WARRANTY_MARK')),
 'vozmozhnosti/'=>array('name'=>'Возможности','title'=>'Возможности интернет-магазина мебели | Мегаполис','content'=>base64_decode('CAPABILITIES_MARK'))
);
foreach($pages as $slug=>$page)if(!is_string($page['content']) || strlen($page['content'])<500)throw new Exception('Content preflight: '.$slug);

$bt=chr(96);
$about='              <li class="footer__list-item"><a href="/o-kompanii/" class="footer__link">['.$bt.'About us'.$bt.']</a></li>';
if(substr_count($prior,$about)!==1)throw new Exception('About footer anchor has changed');
$new=str_replace($about,$about."\n".'              <li class="footer__list-item"><a href="/proizvoditeli/" class="footer__link">Производители</a></li>',$prior);
foreach(array('News','Employees','Jobs','Our shops','Help') as $label){
 $oldLine='              <li class="footer__list-item"><a href="#" class="footer__link">['.$bt.$label.$bt.']</a></li>';
 if(substr_count($new,$oldLine)!==1)throw new Exception('Footer placeholder differs: '.$label);
 $new=str_replace($oldLine,'',$new);
}
foreach(array('Warranty'=>array('/garantiya/','Гарантия'),'Capabilities'=>array('/vozmozhnosti/','Возможности')) as $label=>$link){
 $oldLine='              <li class="footer__list-item"><a href="#" class="footer__link">['.$bt.$label.$bt.']</a></li>';
 if(substr_count($new,$oldLine)!==1)throw new Exception('Footer page link differs: '.$label);
 $new=str_replace($oldLine,'              <li class="footer__list-item"><a href="'.$link[0].'" class="footer__link">'.$link[1].'</a></li>',$new);
}
$help='<div class="footer__title">['.$bt.'Help'.$bt.']</div>';
$pos=strpos($new,$help);
if($pos===false || substr_count($new,$help)!==1)throw new Exception('Help footer column changed');
$start=strrpos(substr($new,0,$pos),'<div class="footer__item col-md-3 col-sm-3">');
$end=strpos($new,'          </div>',$pos);
if($start===false || $end===false || $end-$start>1050)throw new Exception('Help column bounds invalid');
$new=substr($new,0,$start).substr($new,$end+strlen('          </div>'));
$cls='class="footer__item col-md-3 col-sm-3"';
if(substr_count($new,$cls)!==3)throw new Exception('Expected 3 footer columns');
$new=str_replace($cls,'class="footer__item col-md-4 col-sm-4"',$new);
if(strpos($new,'href="#" class="footer__link"')!==false || strpos($new,'/garantiya/')===false || strpos($new,'/proizvoditeli/')===false)throw new Exception('Footer final validation failed');

$created=array();$footerWritten=false;
try{
 foreach($pages as $slug=>$page){
  $row=array('parent_id'=>null,'domain'=>'profikompany.ru','route'=>'*',
    'url'=>$slug,'full_url'=>$slug,'name'=>$page['name'],'title'=>$page['title'],
    'content'=>$page['content'],'create_datetime'=>date('Y-m-d H:i:s'),
    'update_datetime'=>date('Y-m-d H:i:s'),'create_contact_id'=>1,
    'sort'=>100+count($created),'status'=>1,'thumbpage'=>null);
  $id=$pageModel->insert($row);
  if(!(int)$id)throw new Exception('Failed inserting '.$slug);
  $created[$slug]=(int)$id;
 }
 $path=tempnam(dirname($footer),'.footer-stage21-');
 if(!$path || file_put_contents($path,$new)===false)throw new Exception('Failed writing footer');
 $stat=stat($footer);chmod($path,$stat['mode']&0777);
 rename($path,$footer);$footerWritten=true;
 $results=array();
 foreach($pages as $slug=>$p){
  $tmp='/tmp/stage21-smoke-'.getmypid();
  $out=array();$rc=0;
  $uri='https://profikompany.ru/'.$slug.'?stage21_verify=1';
  exec('curl -k -sS -L --max-time 35 -o '.escapeshellarg($tmp)." -w '%{http_code}' ".escapeshellarg($uri),$out,$rc);
  $body=@file_get_contents($tmp);@unlink($tmp);
  $status=trim(implode('',$out));
  $ok=$rc===0 && $status==='200' && $body!==false && strpos($body,$p['title'])!==false;
  $results[]=array('slug'=>$slug,'http'=>$status,'title_found'=>$ok);
  if(!$ok)throw new Exception('New page HTTP smoke test failed '.$slug.' HTTP='.$status);
 }
 $tmp='/tmp/stage21-home-'.getmypid();$out=array();$rc=0;
 exec('curl -k -sS -L --max-time 35 -o '.escapeshellarg($tmp)." -w '%{http_code}' ".escapeshellarg('https://profikompany.ru/?stage21_verify=1'),$out,$rc);
 $body=@file_get_contents($tmp);@unlink($tmp);
 $status=trim(implode('',$out));
 if($rc!==0 || $status!=='200' || !$body)throw new Exception('Homepage response failed');
 $foot=substr($body,strpos($body,'<div class="footer">'));
 foreach(array('/proizvoditeli/','/garantiya/','/vozmozhnosti/') as $frag){
  if(strpos($foot,$frag)===false)throw new Exception('Missing footer link '.$frag);
 }
 if(strpos($foot,'href="https://profikompany.ru/#"')!==false || strpos($foot,'href="#" class="footer__link"')!==false)throw new Exception('Placeholder link still visible');
 echo 'STAGE21_SUCCESS='.json_encode(array('pages'=>$created,'manufacturer_count'=>$count,'checks'=>$results,'footer_http'=>$status),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
}catch(Throwable $e){
 if($footerWritten)file_put_contents($footer,$prior);
 foreach($created as $id)$pageModel->deleteById($id);
 echo 'STAGE21_ROLLBACK='.json_encode(array('error'=>$e->getMessage(),'pages_reverted'=>array_keys($created),'footer_restored'=>$footerWritten),JSON_UNESCAPED_UNICODE)."\n";
 throw $e;
}
?>
PHP
chmod 644 "$tmp"
set +e
su -s /bin/bash web -c "php -d display_errors=1 -d log_errors=0 '$tmp'"
status=$?
set -e
printf 'STAGE21_BACKUP=%s\n' "$backup"
exit "$status"
"""
def run():
 api=os.environ.get('NETANGELS_API_KEY','').strip()
 if not api:raise RuntimeError('NETANGELS_API_KEY absent')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',urllib.parse.urlencode({'api_key':api}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels token unavailable')
 hdr={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 payload=(REMOTE
   .replace('CAPABILITIES_MARK',base64.b64encode((Path(__file__).parent/'pages/vozmozhnosti.html').read_bytes()).decode('ascii'))
   .replace('WARRANTY_MARK',base64.b64encode((Path(__file__).parent/'pages/garantiya.html').read_bytes()).decode('ascii')))
 with tempfile.TemporaryDirectory(prefix='stage21-navigation-') as td:
  key=td+'/id_ed25519'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',key],check=True)
  pub=Path(key+'.pub').read_text().strip()
  data=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'stage21-navigation-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),hdr)
  kid=data.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   r=subprocess.run(['ssh','-i',key,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],input=payload,text=True,capture_output=True,timeout=230)
   for line in r.stdout.splitlines():
    if line.startswith('STAGE21_'):print(line,flush=True)
   if r.returncode:raise RuntimeError('Stage21 deployment failed '+r.stdout[-2300:]+' '+r.stderr[-800:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,hdr)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: Temporary SSH cleanup may need review')
if __name__=='__main__':run()
