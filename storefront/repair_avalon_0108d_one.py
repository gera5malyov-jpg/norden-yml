#!/usr/bin/env python3
"""Guarded one-product extimg URL repair. Production backup and rollback on failure."""
from stage52_disable_callback import execute

REMOTE = r"""set -eu
python3 - <<'PY'
import base64,datetime,hashlib,json,os,pathlib,re,shlex,subprocess,tempfile,time,urllib.request,xml.etree.ElementTree as ET
ROOT=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
SITE='https://profikompany.ru'
PATH='/stol-stol-0108d/'
PRODUCT_ID=1497816
OLD='https://www.avalonmebel.ru/upload/iblock/4cc/4cc538644a80454955d86c5677ccad55.jpg'
NEW='https://www.avalonmebel.ru/upload/iblock/5df/5dfb8d4cc4ae92edb5af75349bcae565.jpg'
EXPECTED='0304f52472db006f912b01ef6784eea694170d6615ce3ad15a6045eb19c2ea2a'
PHP=r'''<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');$m=new waModel();
$id=1497816;
$old='https://www.avalonmebel.ru/upload/iblock/4cc/4cc538644a80454955d86c5677ccad55.jpg';
$new='https://www.avalonmebel.ru/upload/iblock/5df/5dfb8d4cc4ae92edb5af75349bcae565.jpg';
$expected='0304f52472db006f912b01ef6784eea694170d6615ce3ad15a6045eb19c2ea2a';
$mode=$argv[1];$tx=false;
try {
 if($mode==='inspect') {
  $r=$m->query('SELECT id,name,url,summary,image_id,price,count,status FROM shop_product WHERE id=i:id',array('id'=>$id))->fetchAssoc();
  if(!$r)throw new Exception('Product missing');
  $out=array('id'=>$r['id'],'name'=>$r['name'],'url'=>$r['url'],
   'image_id'=>$r['image_id'],'price'=>$r['price'],'count'=>$r['count'],'status'=>$r['status'],
   'summary_b64'=>base64_encode((string)$r['summary']),
   'summary_sha'=>hash('sha256',(string)$r['summary']),
   'internal_photo_count'=>(int)$m->query('SELECT COUNT(*) FROM shop_product_images WHERE product_id=i:id',array('id'=>$id))->fetchField());
  echo 'DB_INSPECT='.json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";exit(0);
 }
 $m->query('START TRANSACTION');$tx=true;
 $r=$m->query('SELECT id,name,url,summary,image_id,price,count,status FROM shop_product WHERE id=i:id FOR UPDATE',array('id'=>$id))->fetchAssoc();
 if(!$r || $r['name']!=='Стол Стол 0108D' || $r['url']!=='stol-stol-0108d' ||
 (int)$r['status']!==1 || (float)$r['price']!==8405.0 || (float)$r['count']!==100.0 || $r['image_id']!==null)
  throw new Exception('Identity or protected product fields changed');
 $source=(string)$r['summary'];
 if($mode==='apply') {
  if(hash('sha256',$source)!==$expected || substr_count($source,$old)!==1 || strpos($source,$new)!==false)
    throw new Exception('Summary source changed from audited image');
  $updated=str_replace($old,$new,$source);
  $m->query('UPDATE shop_product SET summary=s:updated WHERE id=i:id AND summary=s:old',array('updated'=>$updated,'id'=>$id,'old'=>$source));
 } elseif($mode==='rollback') {
  $restore=base64_decode($argv[2],true);
  if($restore===false || hash('sha256',$restore)!==$expected || substr_count($restore,$old)!==1)
   throw new Exception('Backup failed validation');
  if($source!==str_replace($old,$new,$restore))throw new Exception('Concurrent change blocks rollback');
  $m->query('UPDATE shop_product SET summary=s:updated WHERE id=i:id AND summary=s:old',array('updated'=>$restore,'id'=>$id,'old'=>$source));
 } else throw new Exception('Unknown mode');
 $check=$m->query('SELECT summary FROM shop_product WHERE id=i:id',array('id'=>$id))->fetchAssoc();
 if(!$check || $check['summary']!==($mode==='apply'?$updated:$restore))throw new Exception('Compare-and-swap verification failed');
 $m->query('COMMIT');$tx=false;
 echo 'DB_RESULT='.json_encode(array('mode'=>$mode,'id'=>$id,'summary_sha'=>hash('sha256',$check['summary'])))."\n";
} catch(Throwable $e) {
 if($tx)$m->query('ROLLBACK');
 fwrite(STDERR,'DB_GUARD: '.$e->getMessage()."\n");exit(1);
}
'''
def run(cmd,timeout=25):
 p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=timeout)
 return p
with tempfile.NamedTemporaryFile('w',encoding='utf-8',prefix='avalon0108d-',suffix='.php',dir='/tmp',delete=False) as tmp:
 tmp.write(PHP);php_file=tmp.name
os.chmod(php_file,0o644)
def php(mode,summary_b64=None):
 args="php "+shlex.quote(php_file)+" "+shlex.quote(mode)
 if summary_b64 is not None:args+=" "+shlex.quote(summary_b64)
 p=run(['su','-s','/bin/bash','web','-c',args],timeout=26)
 if p.returncode:raise RuntimeError('PHP '+mode+' failed '+p.stderr[-280:])
 prefix='DB_INSPECT=' if mode=='inspect' else 'DB_RESULT='
 rows=[x.split('=',1)[1] for x in p.stdout.splitlines() if x.startswith(prefix)]
 if len(rows)!=1:raise RuntimeError('Missing PHP '+mode+' report')
 return json.loads(rows[0])
def request(url):
 p=run(['curl','-ksSL','--max-time','24','-w','\n__CODE__%{http_code}',url],timeout=29)
 if p.returncode or '\n__CODE__' not in p.stdout:raise RuntimeError('HTTP request failed '+url[:70])
 data,status=p.stdout.rsplit('\n__CODE__',1)
 return data,status
folder=None;applied=False
try:
 # Preflight 1: exact database identity, state and originally audited source.
 before=php('inspect');orig=base64.b64decode(before['summary_b64'])
 if(before['id']!=str(PRODUCT_ID) or before['name']!='Стол Стол 0108D'
    or before['url']!='stol-stol-0108d' or before['summary_sha']!=EXPECTED
    or orig.count(OLD.encode())!=1 or NEW.encode() in orig or before['internal_photo_count']!=0
    or str(before['status'])!='1' or float(before['price'])!=8405 or float(before['count'])!=100):
  raise RuntimeError('PREFLIGHT 1 database source diverged')
 print('AVALON_PREFLIGHT='+json.dumps({'check':1,'result':'passed','product_id':PRODUCT_ID,'summary_sha':EXPECTED}),flush=True)
 # Preflight 2: latest published Avalon YML contains this product and exactly the new picture.
 feed=subprocess.run(['curl','-fsSL','--max-time','30','https://raw.githubusercontent.com/gera5malyov-jpg/norden-yml/main/avalon-podstolya/catalog.yml'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=35)
 if feed.returncode:raise RuntimeError('PREFLIGHT 2 cannot read current feed')
 offers=ET.fromstring(feed.stdout).findall('.//offer')
 target=[o for o in offers if o.get('id')=='avalon-table--0108d']
 if len(target)!=1 or target[0].findtext('url')!='https://www.avalonmebel.ru/catalog/stoly-dlya-kafe/-0108d' or [x.text for x in target[0].findall('picture')]!=[NEW]:
  raise RuntimeError('PREFLIGHT 2 source YML differs')
 print('AVALON_PREFLIGHT='+json.dumps({'check':2,'result':'passed','feed_offers':len(offers)}),flush=True)
 # Preflight 3: public page still has the broken photo; source photo is reliably available.
 html,status=request(SITE+PATH)
 if status!='200' or ('src="'+OLD+'"') not in html or ('src="'+NEW+'"') in html:
  raise RuntimeError('PREFLIGHT 3 live page source differs')
 for i in range(3):
  p=run(['curl','-ksSL','--max-time','15','-o','/dev/null','-w','%{http_code} %{content_type}',NEW],timeout=19)
  if p.returncode or not p.stdout.startswith('200 image/jpeg'):raise RuntimeError('PREFLIGHT 3 new image unavailable')
 print('AVALON_PREFLIGHT='+json.dumps({'check':3,'result':'passed','replacement_http':'200 image/jpeg','photo_checks':3}),flush=True)
 # Private backup of exactly one original field.
 folder=ROOT.parent/'storefront-backups'/('avalon0108d-extimg-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
 folder.mkdir(parents=True,mode=0o700)
 oldfile=folder/'product-summary.before';oldfile.write_bytes(orig);os.chmod(str(oldfile),0o600)
 (folder/'audit.json').write_text(json.dumps({'id':PRODUCT_ID,'old_sha':EXPECTED,'new_sha':hashlib.sha256(orig.replace(OLD.encode(),NEW.encode())).hexdigest()}))
 os.chmod(str(folder/'audit.json'),0o600)
 result=php('apply');applied=True
 new_sha=hashlib.sha256(orig.replace(OLD.encode(),NEW.encode())).hexdigest()
 if result['summary_sha']!=new_sha:raise RuntimeError('Database update mismatch')
 print('AVALON_UPDATED='+json.dumps({'product_id':PRODUCT_ID,'field':'shop_product.summary','new_sha':new_sha,'backup':str(folder)}),flush=True)
 # Ten independent public checks, fresh requests. No orders or customer changes.
 for i in range(1,11):
  html,status=request(SITE+PATH+'?extimg_check='+str(os.getpid())+'_'+str(i))
  passed=(status=='200' and ('src="'+NEW+'"') in html and ('src="'+OLD+'"') not in html
      and 'js-extimg-main-img' in html and 'Стол Стол 0108D' in html
      and 'megapolis-mobile-buy' in html)
  print('AVALON_TEST='+json.dumps({'round':i,'ok':bool(passed),'http':status,'new_photo':('src="'+NEW+'"') in html,'old_photo':('src="'+OLD+'"') in html}),flush=True)
  if not passed:raise RuntimeError('Public photo acceptance failed at round '+str(i))
 after=php('inspect')
 if after['summary_sha']!=new_sha or after['price']!=before['price'] or after['count']!=before['count'] or after['status']!=before['status'] or after['name']!=before['name'] or after['image_id']!=before['image_id']:
  raise RuntimeError('Protected product fields changed after deployment')
 print('AVALON_DONE='+json.dumps({'ok':True,'tests_passed':10,'tests_total':10,'product_id':PRODUCT_ID,'backup':str(folder)}),flush=True)
except Exception as e:
 if applied:
  try:
   restored=php('rollback',base64.b64encode((folder/'product-summary.before').read_bytes()).decode())
   print('AVALON_ROLLBACK='+json.dumps({'restored':True,'summary_sha':restored['summary_sha'],'reason':str(e)[:200]}),flush=True)
  except Exception as rollback_error:
   print('AVALON_ROLLBACK='+json.dumps({'restored':False,'reason':str(rollback_error)[:180],'backup':str(folder)}),flush=True)
 raise
finally:
 os.unlink(php_file)
PY
"""
if __name__ == "__main__":
    result=execute(REMOTE)
    labels=("AVALON_PREFLIGHT=","AVALON_UPDATED=","AVALON_TEST=","AVALON_DONE=","AVALON_ROLLBACK=")
    for line in result.splitlines():
        if line.startswith(labels):print(line,flush=True)
    if "AVALON_DONE=" not in result:
        raise RuntimeError("Guarded patch did not complete all ten tests")
