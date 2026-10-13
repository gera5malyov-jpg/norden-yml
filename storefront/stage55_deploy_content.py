#!/usr/bin/env python3
"""Phase 2 guarded rollout. No action occurs on import; --rollback is CAS only."""
import base64,hashlib,json,re,sys
from html.parser import HTMLParser
from pathlib import Path
BASE=Path(__file__).parent
SITE='wa-data/public/site/themes/pureMegapolis42/'
SHOP='wa-data/public/shop/themes/pureMegapolis42/'
EXPECTED_FILES={
 SITE+'index.html':'8aac2e31d06d5afbe5688b1e67b4b0f5b5a7dec5f6b3fffac1cc3012ec3e7a28',
 SHOP+'product.html':'d8b84f04d936891d2a8595a979c2e93f169199096acba896909d79b44f861abf',
 SHOP+'order.html':'d6a1427790068795c5ee133de3519a4cec9fe780464bfb4dd3221309771b0be9',
 'wa-apps/shop/lib/config/shopSitemapConfig.class.php':'466419d4915e38d8d898fdcae1d387ddcc966fa499d9fdcb728a09a13e3a1b57',
}
# Populated from the audited raw CRLF CMS content, never universal-newline IO.
DELIVERY={'id': 12, 'domain': 'profikompany.ru', 'full_url': 'dostavka/', 'old_sha': '01d4dbcdb3194ce04c524eeaa31dacac13538dbeb887e5773d7eaf2cd4c7d4cc', 'new': '<h2></h2>\r\n\r\n<h2>Доставка</h2>\r\n\r\n<p>Мы доставляем мебель аккуратно и предсказуемо: заранее согласуем дату, подскажем оптимальный способ доставки и держим вас в курсе на каждом этапе.</p>\r\n\r\n<h3>Как проходит доставка</h3>\r\n\r\n<ul>  \r\n\r\n<li><strong>Дату и интервал доставки</strong> обязательно согласуем с вами после подтверждения заказа.</li>  \r\n\r\n<li><strong>Накануне</strong> вы получите <strong>SMS-уведомление</strong>.</li>  \r\n\r\n<li>Курьер <strong>позвонит за 30 минут</strong> до прибытия.</li>  \r\n\r\n<li>Если время доставки неудобно &mdash; поможем <strong>перенести дату</strong> (контакты для связи сообщит менеджер/будут в уведомлении).</li>  \r\n\r\n</ul>\r\n\r\n<h2>Доставка по Санкт-Петербургу</h2>\r\n\r\n<ul>  \r\n\r\n<li>Стоимость доставки рассчитывается <strong>индивидуально</strong>: товары отправляются из разных городов, поэтому расчёт зависит от <strong>города отправки, адреса получения, веса и объёма заказа</strong>.</li>  \r\n\r\n<li>Дополнительные услуги (подъём/сборка) также рассчитываются и согласуются отдельно.</li>  \r\n\r\n<li>Точную стоимость и условия сообщит менеджер при подтверждении заказа.</li>  \r\n\r\n</ul>\r\n\r\n<h2>Самовывоз / пункт выдачи</h2>\r\n\r\n<ul>  \r\n\r\n<li>Доставка до пункта выдачи/самовывоза рассчитывается <strong>индивидуально</strong> &mdash; зависит от веса и объёма.</li>  \r\n\r\n<li>Перемещение товара в пункт выдачи осуществляется <strong>после 100% предоплаты</strong>.</li>  \r\n\r\n<li>Когда заказ прибудет в пункт выдачи, вы получите <strong>SMS-уведомление</strong>.</li>  \r\n\r\n</ul>\r\n\r\n<h2>Доставка по России</h2>\r\n\r\n<p>Отправляем заказы по России через проверенные транспортные компании. Подберём оптимальный вариант по цене и срокам, а также оформим доставку до терминала или до двери.</p>\r\n\r\n<p>Работаем с ТК (в зависимости от направления и типа груза):</p>\r\n\r\n<ul>  \r\n\r\n<li><strong>ПЭК</strong></li>  \r\n\r\n<li><strong>Деловые линии</strong></li>  \r\n\r\n<li><strong>СДЭК</strong></li>  \r\n\r\n<li><strong>DPD</strong></li>  \r\n\r\n<li><strong>Байкал Сервис</strong></li>  \r\n\r\n</ul>\r\n\r\n<p>Стоимость доставки по России рассчитывается индивидуально &mdash; по <strong>городу, габаритам, весу и выбранным услугам</strong> (до терминала/до двери). Менеджер рассчитает и согласует стоимость до отправки.</p>\r\n\r\n<h2>Подъём и сборка</h2>\r\n\r\n<ul>  \r\n\r\n<li><strong>Сборка мебели</strong> доступна по желанию клиента.</li>  \r\n\r\n<li>При заказе сборки услуга <strong>подъёма является обязательной</strong>, так как это требуется для выполнения работ на месте.</li>  \r\n\r\n<li>Стоимость подъёма/сборки рассчитывается <strong>индивидуально</strong> и согласуется заранее.</li>  \r\n\r\n</ul>\r\n\r\n<h2>Упаковка, сохранность и отслеживание</h2>\r\n\r\n<ul>  \r\n\r\n<li>Для каждого отправления выдаём <strong>трек-номер</strong>, чтобы вы могли отслеживать статус доставки.</li>  \r\n\r\n<li>Мы уделяем внимание сохранности товара: при необходимости предлагаем <strong>усиленную упаковку/обрешётку</strong> и <strong>страхование</strong> &mdash; условия зависят от выбранной ТК и согласуются перед отправкой.</li>  \r\n\r\n</ul>\r\n\r\n<p></p>\r\n\r\n<p><!-- The widget comes here --></p>\r\n\r\n<p id="megapolis-delivery-calculator-note">Калькулятор даёт ориентир для предварительного расчёта. По умолчанию выбран город отправки Москва; фактический город отправки зависит от товара и может отличаться. Уточните его у менеджера и укажите в калькуляторе. Окончательная стоимость доставки согласуется отдельно.</p>\r\n\r\n<div id="transportnye-kompanii"></div>\r\n\r\n<script src="https://transportnye-kompanii.com/transport/generate-calculator/"></script>\r\n\r\n<script>calculatorBuilder({\r\n    companies: [\r\n        [\'baikalService\', \'Байкал Сервис\'],\r\n        [\'pec\', \'ПЭК\'],\r\n        [\'cdek\', \'СДЭК\'],\r\n        [\'dellin\', \'Деловые линии\'],\r\n        [\'dpd\', \'DPD\'],\r\n    ],\r\n    title: \'Калькулятор стоимости доставки\',\r\n    cityFrom: \'Москва\',\r\n    cargoPickup: true,\r\n    cargoDelivery: true,\r\n    insurance: true,\r\n    rigidPackaging: true,\r\n    colorScheme: \'#62488E\',\r\n    buttonTextColor: \'white\',\r\n});\r\n</script>\r\n\r\n<p>Мы выдаём трекинг-номер каждой посылке, поэтому вы всегда сможете увидеть статус доставки.</p>\r\n\r\n<p>Вы получите sms-уведомление, когда заказ пребудет на пункт самовывоза.</p>\r\n\r\n<p>Мы заботимся о целостности товара и страхуем все заказы. Особо ценный товар мы обрешечиваем.</p>\r\n\r\n<p>Все отправленные заказы застрахованы на сумму заказанного изделия.</p>'}

class SafeDescription(HTMLParser):
 def handle_starttag(self,tag,attrs):
  if tag not in ('p','ul','li') or attrs:raise ValueError('Unexpected description HTML')
 def handle_endtag(self,tag):
  if tag not in ('p','ul','li'):raise ValueError('Unexpected description HTML')
 def handle_startendtag(self,tag,attrs):raise ValueError('Unexpected description HTML')

def validate_proposal(proposal):
 if proposal.get('site')!='https://profikompany.ru' or proposal.get('schema_version')!=1:raise ValueError('Wrong proposal site/version')
 if len(proposal.get('products',[]))!=50 or not 1<=len(proposal.get('categories',[]))<=6:raise ValueError('Unreviewed proposal size')
 seen=set()
 for kind,allowed in (('products',{'description'}),('categories',{'meta_title','meta_description'})):
  for row in proposal[kind]:
   key=(kind,row['id'])
   if not isinstance(row['id'],int) or row['id']<=0 or key in seen:raise ValueError('Invalid or duplicate ID')
   seen.add(key)
   if not row.get('set') or not set(row['set'])<=allowed:raise ValueError('Disallowed database field')
   if kind=='products':
    e=row['expected']
    if int(e.get('status',0))!=1 or int(e.get('category_id') or 0)<=0 or float(e.get('price') or 0)<=0 or float(e.get('count') or 0)<=0:raise ValueError('Product not active/in stock/categorized')
    if str(e.get('description') or '').strip():raise ValueError('Existing description may not be replaced')
    if not re.fullmatch('[0-9a-f]{64}',e.get('features_sha256','')):raise ValueError('Feature digest absent')
    value=row['set']['description'];SafeDescription().feed(value)
    if not 4<=value.count('<li>')<=6:raise ValueError('Description must contain 4–6 verified specifications')
   else:
    for field in row['set']:
     if str(row['expected'].get(field) or '').strip():raise ValueError('Existing category metadata may not be replaced')
 return proposal

PHP = r'''<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';waSystem::getInstance(null,new SystemConfig());wa('shop');$m=new waModel();
$input=json_decode(base64_decode('DB_INPUT_B64'),true);$mode=$input['mode'];$payload=$input['payload'];
function canon($x){if(is_array($x)){if(array_keys($x)!==range(0,count($x)-1)){ksort($x,SORT_STRING);}foreach($x as $k=>$v){$x[$k]=canon($v);}}return $x;}
function scalar_equal($a,$b){return ($a===null || $b===null)?$a===$b:(string)$a===(string)$b;}
function field_condition($field,$value,&$params,$key){if($value===null)return $field.' IS NULL';$params[$key]=$value;return $field.'=s:'.$key;}
function write_field($m,$record,$from,$to){
 $allowed=array('shop_product'=>array('description'),'shop_category'=>array('meta_title','meta_description'),'shop_page'=>array('content'));
 $table=$record['table'];$field=$record['field'];if(!isset($allowed[$table])||!in_array($field,$allowed[$table],true))throw new Exception('Forbidden write field');
 $params=array('id'=>$record['id'],'new_value'=>$record[$to]);$condition=field_condition($field,$record[$from],$params,'old_value');
 $assignment=$record[$to]===null?$field.'=NULL':$field.'=s:new_value';
 $sql='UPDATE '.$table.' SET '.$assignment.' WHERE id=i:id AND '.$condition;
 $m->query($sql,$params);
}
function prepare_records($m,$payload){
 $records=array();$skipped=array('products'=>0,'categories'=>0);
 foreach($payload['proposal']['products'] as $p){
  $e=$p['expected'];$r=$m->query('SELECT id,name,url,category_id,status,price,count,description FROM shop_product WHERE id=i:id FOR UPDATE',array('id'=>$p['id']))->fetchAssoc();$ok=(bool)$r;
  foreach(array('name','url','category_id','status','price','count','description') as $field){if(!$r || !scalar_equal($r[$field],$e[$field]))$ok=false;}
  if(!$r || (int)$r['category_id']<=0 || (int)$r['status']!==1 || (float)$r['price']<=0 || $r['count']===null || (float)$r['count']<=0 || trim((string)$r['description'])!=='')$ok=false;
  if($r){$c=$m->query('SELECT id,status,full_url FROM shop_category WHERE id=i:id FOR UPDATE',array('id'=>$r['category_id']))->fetchAssoc();if(!$c || (int)$c['status']!==1 || $c['full_url']!==$e['category_url'])$ok=false;}
  if($ok){
   $m->query('SELECT id FROM shop_product_skus WHERE product_id=i:id FOR UPDATE',array('id'=>$p['id']))->fetchAll();
   $m->query('SELECT product_id FROM shop_product_features WHERE product_id=i:id FOR UPDATE',array('id'=>$p['id']))->fetchAll();
   $fresh=new shopProduct($p['id']);$features=json_decode(json_encode($fresh->features,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES),true);
   $digest=hash('sha256',json_encode(canon($features),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES));if($digest!==$e['features_sha256'])$ok=false;
   $skus=$fresh->skus;$eligible=false;
   if(count($skus)!==count($e['sku_availability']))$ok=false;
   foreach($e['sku_availability'] as $expected){$sku=isset($skus[$expected['id']])?$skus[$expected['id']]:null;
    if(!$sku){$ok=false;continue;}
    foreach(array('id','count','available') as $key){if(!scalar_equal($sku[$key],$expected[$key]))$ok=false;}
    if((float)$sku['price']!==(float)$expected['price'])$ok=false;
    if((int)$sku['available']===1 && (float)$sku['price']>0 && $sku['count']!==null && (float)$sku['count']>0)$eligible=true;
   }
   if(!$eligible)$ok=false;
  }
  if(!$ok){$skipped['products']++;continue;}
  $records[]=array('table'=>'shop_product','id'=>$p['id'],'field'=>'description','old'=>$r['description'],'new'=>$p['set']['description']);
 }
 foreach($payload['proposal']['categories'] as $p){
  $r=$m->query('SELECT id,name,full_url,status,meta_title,meta_description FROM shop_category WHERE id=i:id FOR UPDATE',array('id'=>$p['id']))->fetchAssoc();$ok=(bool)$r;
  if(!$r || (int)$r['status']!==1)$ok=false;
  foreach($p['expected'] as $field=>$value){if(!$r || !scalar_equal($r[$field],$value))$ok=false;}
  if(!$ok){$skipped['categories']++;continue;}
  foreach($p['set'] as $field=>$value){if(!in_array($field,array('meta_title','meta_description'),true)||trim((string)$r[$field])!=='')throw new Exception('Category field not blank');$records[]=array('table'=>'shop_category','id'=>$p['id'],'field'=>$field,'old'=>$r[$field],'new'=>$value);}
 }
 $d=$payload['delivery'];$r=$m->query('SELECT id,domain,full_url,content FROM shop_page WHERE id=i:id FOR UPDATE',array('id'=>$d['id']))->fetchAssoc();
 if(!$r || $r['domain']!==$d['domain'] || $r['full_url']!==$d['full_url'] || hash('sha256',$r['content'])!==$d['old_sha'])throw new Exception('Audited delivery CMS page changed');
 $records[]=array('table'=>'shop_page','id'=>$d['id'],'field'=>'content','old'=>$r['content'],'new'=>$d['new']);
 return array('records'=>$records,'skipped'=>$skipped);
}
try{
 $m->query('START TRANSACTION');
 if($mode==='rollback'){
  $restored=0;$blocked=0;
  foreach(array_reverse($input['records']) as $record){
   $allowed=array('shop_product'=>array('description'),'shop_category'=>array('meta_title','meta_description'),'shop_page'=>array('content'));
   if(!isset($allowed[$record['table']])||!in_array($record['field'],$allowed[$record['table']],true))throw new Exception('Invalid rollback field');
   $r=$m->query('SELECT '.$record['field'].' FROM '.$record['table'].' WHERE id=i:id FOR UPDATE',array('id'=>$record['id']))->fetchAssoc();
   if($r && scalar_equal($r[$record['field']],$record['old']))continue;
   if(!$r || !scalar_equal($r[$record['field']],$record['new'])){$blocked++;continue;}
   write_field($m,$record,'new','old');$restored++;
  }
  $result=array('restored'=>$restored,'blocked'=>$blocked);$m->query('COMMIT');
 }else{
  $result=prepare_records($m,$payload);
  if($mode==='apply'){
   if(canon($result['records'])!==canon($input['records']))throw new Exception('Content changed after backup; no database writes');
   foreach($result['records'] as $record)write_field($m,$record,'old','new');$m->query('COMMIT');
   $ids=array('products'=>array(),'categories'=>array());
   foreach($result['records'] as $record){if($record['table']==='shop_product')$ids['products'][]=$record['id'];if($record['table']==='shop_category')$ids['categories'][]=$record['id'];}
   $result=array('applied_fields'=>count($result['records']),'skipped'=>$result['skipped'],'applied_product_ids'=>array_values(array_unique($ids['products'])),'applied_category_ids'=>array_values(array_unique($ids['categories'])));
  }elseif($mode==='prepare'){$m->query('ROLLBACK');}else{throw new Exception('Unknown mode');}
 }
 echo 'DB_RESULT='.base64_encode(json_encode($result,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES))."\n";
}catch(Exception $e){$m->query('ROLLBACK');fwrite(STDERR,$e->getMessage()."\n");exit(1);}
'''

REMOTE_COMMON = r'''
import base64,hashlib,json,os,pathlib,pwd,shutil,subprocess,tempfile,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
def digest(data):return hashlib.sha256(data).hexdigest()
def db_run(mode,payload,records=None):
 data={'mode':mode,'payload':payload,'records':records or []}
 script=payload['php'].replace('DB_INPUT_B64',base64.b64encode(json.dumps(data,ensure_ascii=False).encode('utf-8')).decode('ascii'))
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',prefix='stage55-db-',suffix='.php',delete=False) as f:f.write(script);name=f.name
 os.chmod(name,0o644)
 try:
  lint=subprocess.run(['php','-l',name],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=20)
  if lint.returncode:raise RuntimeError('DB PHP lint failed')
  p=subprocess.run(['su','-s','/bin/bash','web','-c','php '+name],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=70)
  if p.returncode:raise RuntimeError('DB action failed: '+p.stderr[-250:])
  lines=[s.split('=',1)[1] for s in p.stdout.splitlines() if s.startswith('DB_RESULT=')]
  if len(lines)!=1:raise RuntimeError('Missing database action result')
  return json.loads(base64.b64decode(lines[0]).decode('utf-8'))
 finally:os.unlink(name)
def http(route):
 p=subprocess.run(['curl','-ksSL','--max-time','18','-w','\n%{http_code}','https://profikompany.ru'+route],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=22)
 body,status=p.stdout.rsplit('\n',1) if '\n' in p.stdout else ('','')
 if p.returncode or status!='200':raise RuntimeError('HTTP check failed '+route.split('?',1)[0])
 return body
def sitemap_stats():
 import concurrent.futures,collections,xml.etree.ElementTree as ET
 ns={'s':'http://www.sitemaps.org/schemas/sitemap/0.9'}
 children=[x.text for x in ET.fromstring(http('/sitemap.xml')).findall('s:sitemap/s:loc',ns)]
 if not children or any(not u.startswith('https://profikompany.ru/') for u in children):raise RuntimeError('Unexpected Sitemap children')
 def one(url):
  body=http(url[len('https://profikompany.ru'):]);doc=ET.fromstring(body)
  return [x.text for x in doc.findall('s:url/s:loc',ns)]
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:lists=list(pool.map(one,children))
 all_urls=set();inside=0;across=0;entries=0
 for urls in lists:
  unique=set(urls);inside+=len(urls)-len(unique);across+=len(unique & all_urls);all_urls.update(unique);entries+=len(urls)
 return {'children':len(children),'entries':entries,'unique_urls':len(all_urls),'duplicates_inside':inside,'duplicates_across':across}
def acquire_rollout_lock(folder,timeout=120):
 import fcntl
 fd=os.open(str(folder/'.rollout.lock'),os.O_CREAT|os.O_RDWR,0o600)
 deadline=time.monotonic()+timeout
 while True:
  try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);return fd
  except BlockingIOError:
   if time.monotonic()>=deadline:os.close(fd);raise RuntimeError('Previous phase 2 mutation still running; recovery state retained')
   time.sleep(0.25)
def restore_files(folder,records):
 restored=0;blocked=[]
 for row in reversed(records):
  path=root/row['path']
  if not path.exists() and row['backup'] is None:continue
  if path.exists() and digest(path.read_bytes())==row['old_sha']:continue
  if not path.exists() or digest(path.read_bytes())!=row['new_sha']:blocked.append(row['path']);continue
  if row['backup']:
   shutil.copy2(str(folder/row['backup']),str(path));os.utime(str(path),None)
  else:path.unlink()
  restored+=1
 return {'restored':restored,'blocked':blocked}
'''
REMOTE_PREPARE=r'''
payload=json.loads(base64.b64decode('PAYLOAD_B64').decode('utf-8'))
site='wa-data/public/site/themes/pureMegapolis42/';shop='wa-data/public/shop/themes/pureMegapolis42/'
changes={}
with tempfile.TemporaryDirectory(prefix='stage55-modules-') as td:
 for name,source in payload['modules'].items():(pathlib.Path(td)/name).write_text(source,encoding='utf-8')
 import sys
 sys.path.insert(0,td)
 from stage56_delivery import patch_product,patch_order
 from stage55_seo_content import build_sitemap_duplicate_patch,patch_category_metadata_head
 for rel,expected in payload['expected_files'].items():
  if digest((root/rel).read_bytes())!=expected:raise RuntimeError('Phase 1 prerequisite/source mismatch: '+rel)
 changes[shop+'product.html']=patch_product((root/(shop+'product.html')).read_text(encoding='utf-8')).encode('utf-8')
 changes[shop+'order.html']=patch_order((root/(shop+'order.html')).read_text(encoding='utf-8')).encode('utf-8')
 core='wa-apps/shop/lib/config/shopSitemapConfig.class.php'
 changes[core]=build_sitemap_duplicate_patch((root/core).read_text(encoding='utf-8')).encode('utf-8')
 index=(root/(site+'index.html')).read_text(encoding='utf-8')
 index=patch_category_metadata_head(index,payload['proposal']['categories'])
 anchor='<script src="{$wa_real_theme_url}js/megapolis-funnel-v2.js?v=20261012"></script>'
 if index.count(anchor)!=1 or 'megapolis-delivery-v1.js' in index:raise RuntimeError('Phase 1 index anchor mismatch')
 fresh_funnel=anchor.replace('v=20261012','v=20261013a')
 changes[site+'index.html']=index.replace(anchor,fresh_funnel+'\n<script src="{$wa_real_theme_url}js/megapolis-delivery-v1.js?v=20261013a" defer></script>',1).encode('utf-8')
 for rel,text in payload['assets'].items():
  if (root/rel).exists():raise RuntimeError('New asset already exists')
  changes[rel]=text.encode('utf-8')
 with tempfile.NamedTemporaryFile('wb',suffix='.php',delete=False) as f:f.write(changes[core]);php_file=f.name
 try:
  lint=subprocess.run(['php','-l',php_file],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=20)
  if lint.returncode:raise RuntimeError('Sitemap PHP lint failed; no writes')
 finally:os.unlink(php_file)
 baseline=sitemap_stats()
 prepared=db_run('prepare',payload)
 folder=root.parent/'storefront-backups'/('stage55-content-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
 folder.mkdir(mode=0o700,parents=True);web=pwd.getpwnam('web');os.chown(str(folder),web.pw_uid,web.pw_gid)
 records=[]
 for i,(rel,data) in enumerate(changes.items()):
  path=root/rel;before=path.read_bytes() if path.exists() else None;name=str(i)+'.bak'
  if rel in payload['expected_files'] and (before is None or digest(before)!=payload['expected_files'][rel]):raise RuntimeError('Concurrent source edit before backup')
  if rel in payload['assets'] and before is not None:raise RuntimeError('Concurrent asset creation before backup')
  if before is not None:
   shutil.copy2(str(path),str(folder/name));os.chmod(str(folder/name),0o600);os.chown(str(folder/name),web.pw_uid,web.pw_gid)
  new_name=str(i)+'.new';(folder/new_name).write_bytes(data);os.chmod(str(folder/new_name),0o600);os.chown(str(folder/new_name),web.pw_uid,web.pw_gid)
  records.append({'path':rel,'backup':name if before is not None else None,'new_backup':new_name,'old_sha':digest(before) if before is not None else None,'new_sha':digest(data)})
 for name,obj in [('manifest.json',records),('database.json',prepared['records']),('baseline-sitemap.json',baseline)]:
  p=folder/name;p.write_text(json.dumps(obj,ensure_ascii=False),encoding='utf-8');os.chmod(str(p),0o600);os.chown(str(p),web.pw_uid,web.pw_gid)
 print('STAGE55_PREPARED='+json.dumps({'backup':str(folder),'planned_db_fields':len(prepared['records'])}),flush=True)
'''
REMOTE_APPLY=r'''

payload=json.loads(base64.b64decode('PAYLOAD_B64').decode('utf-8'))
folder=pathlib.Path('BACKUP_FOLDER')
_rollout_lock=acquire_rollout_lock(folder)
records=json.loads((folder/'manifest.json').read_text())
db_records=json.loads((folder/'database.json').read_text())
for row in records:
 path=root/row['path'];before=path.read_bytes() if path.exists() else None
 if (digest(before) if before is not None else None)!=row['old_sha']:raise RuntimeError('Concurrent file change before apply')
 if digest((folder/row['new_backup']).read_bytes())!=row['new_sha']:raise RuntimeError('Prepared bytes changed')
# The caller persisted recovery identity before this potentially ambiguous COMMIT.
db_result=db_run('apply',payload,db_records)
(folder/'applied.json').write_text(json.dumps(db_result),encoding='utf-8');os.chmod(str(folder/'applied.json'),0o600)
for row in records:
 path=root/row['path'];before=path.read_bytes() if path.exists() else None
 if (digest(before) if before is not None else None)!=row['old_sha']:raise RuntimeError('Concurrent file edit immediately before replace')
 stat=path.stat() if path.exists() else (root/'wa-data/public/site/themes/pureMegapolis42/index.html').stat()
 with tempfile.NamedTemporaryFile('wb',dir=str(path.parent),prefix='.stage55-',delete=False) as f:f.write((folder/row['new_backup']).read_bytes());tmp=f.name
 os.chmod(tmp,(stat.st_mode & 0o777) if row['backup'] else 0o644);os.chown(tmp,stat.st_uid,stat.st_gid);os.replace(tmp,str(path));os.utime(str(path),None)
print('STAGE55_APPLIED='+json.dumps({'backup':str(folder),'database':db_result,'files':len(records)},ensure_ascii=False),flush=True)
'''
REMOTE_VERIFY=r'''

payload=json.loads(base64.b64decode('PAYLOAD_B64').decode('utf-8'))
folder=pathlib.Path('BACKUP_FOLDER')
records=json.loads((folder/'manifest.json').read_text())
db_result=json.loads((folder/'applied.json').read_text())
baseline=json.loads((folder/'baseline-sitemap.json').read_text())
applied_products=[r for r in payload['proposal']['products'] if r['id'] in db_result['applied_product_ids']]
applied_categories=[r for r in payload['proposal']['categories'] if r['id'] in db_result['applied_category_ids']]
routes=['/','/dostavka/']+[r['url'] for r in applied_categories]+[r['url'] for r in applied_products[:3]]
def check_route(route):
 body=http(route+'?content55='+str(os.getpid()))
 ok='2449264' in body and '6776332' in body and 'id="callback-modal"' not in body and 'Заказать звонок' not in body and 'megapolis-delivery-v1.js' in body
 if route=='/dostavka/':ok=ok and 'megapolis-delivery-calculator-note' in body and 'от 600' not in body
 if route in [r['url'] for r in applied_products[:3]]:ok=ok and 'megapolis-buy-to-cart-v1.js' in body
 return {'route':route,'ok':ok}
import concurrent.futures
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:checks=list(pool.map(check_route,routes))
if not all(r['ok'] for r in checks):raise RuntimeError('Content smoke failed')
after=sitemap_stats()
if after['children']!=baseline['children'] or after['unique_urls']<baseline['unique_urls']*0.999 or after['duplicates_inside']>baseline['duplicates_inside']:raise RuntimeError('Sitemap postpatch guard failed')
if baseline['duplicates_inside'] and after['duplicates_inside']>=baseline['duplicates_inside']:raise RuntimeError('Sitemap duplicate suppression not observed')
p=folder/'after-sitemap.json';p.write_text(json.dumps(after),encoding='utf-8');os.chmod(str(p),0o600)
summary={'success':True,'backup':str(folder),'files':len(records),'database':db_result,'sitemap_before':baseline,'sitemap_after':after,'http_checks':len(checks)}
print('STAGE55_DEPLOY='+json.dumps(summary,ensure_ascii=False),flush=True)
'''

def build_payload():
 from stage53_deploy_sales import server_module
 proposal=validate_proposal(json.loads((BASE/'stage55-proposal.json').read_text()))
 return {'proposal':proposal,'delivery':DELIVERY,'expected_files':EXPECTED_FILES,'php':PHP,
         'modules':{name:server_module((BASE/name).read_text()) for name in ('stage55_seo_content.py','stage56_delivery.py')},
         'assets':{SITE+'js/megapolis-delivery-v1.js':(BASE/'assets/megapolis-delivery-v1.js').read_text()}}

def remote_stage(code,payload,folder=None):
 if folder is not None and not re.fullmatch('/home/web/vm-23f9aff9.na4u.ru/storefront-backups/stage55-content-[A-Za-z0-9-]+',folder):raise ValueError('Invalid backup path')
 encoded=base64.b64encode(json.dumps(payload,ensure_ascii=False).encode()).decode()
 script=code.replace('PAYLOAD_B64',encoded)
 if folder is not None:script=script.replace('BACKUP_FOLDER',folder)
 return "set -eu\npython3 - <<'PY'\n"+REMOTE_COMMON+script+"\nPY\n"


def rollback_remote(folder):
 if not re.fullmatch('/home/web/vm-23f9aff9.na4u.ru/storefront-backups/stage55-content-[A-Za-z0-9-]+',folder):raise ValueError('Invalid backup path')
 payload={'php':PHP};encoded=base64.b64encode(json.dumps(payload).encode()).decode()
 code="\nfolder=pathlib.Path("+repr(folder)+")\npayload=json.loads(base64.b64decode('"+encoded+"').decode('utf-8'))\n"
 code+="_rollout_lock=acquire_rollout_lock(folder)\nrecords=json.loads((folder/'manifest.json').read_text())\ndb_records=json.loads((folder/'database.json').read_text())\nfiles=restore_files(folder,records)\ndatabase=db_run('rollback',payload,db_records)\nprint('STAGE55_ROLLBACK='+json.dumps({'files':files,'database':database}),flush=True)\nif files['blocked'] or database['blocked']:raise RuntimeError('Concurrent changes blocked some restoration; preserved user edits')\n"
 return "set -eu\npython3 - <<'PY'\n"+REMOTE_COMMON+code+"\nPY\n"

def main():
 from stage52_disable_callback import execute
 state=Path('stage55-content-state.json');evidence=Path('storefront-stage55-evidence');evidence.mkdir(exist_ok=True)
 if '--rollback' in sys.argv:
  if not state.exists():print('No completed phase 2 state; no rollback required');return
  out=execute(rollback_remote(json.loads(state.read_text())['backup']))
  for line in out.splitlines():
   if line.startswith('STAGE55_'):print(line,flush=True)
  state.unlink();return
 payload=build_payload()
 if state.exists():raise RuntimeError('Existing phase 2 recovery state; review/rollback before another rollout')
 def parse_marker(out,prefix):
  rows=[json.loads(line.split('=',1)[1]) for line in out.splitlines() if line.startswith(prefix+'=')]
  if len(rows)!=1:raise RuntimeError('Missing remote stage marker '+prefix)
  return rows[0]
 prepared=parse_marker(execute(remote_stage(REMOTE_PREPARE,payload)),'STAGE55_PREPARED')
 # Persist the private recovery folder BEFORE any database or theme mutation.
 state.write_text(json.dumps({'backup':prepared['backup']}))
 print('STAGE55_PREPARED='+json.dumps(prepared),flush=True)
 try:
  applied=parse_marker(execute(remote_stage(REMOTE_APPLY,payload,prepared['backup'])),'STAGE55_APPLIED')
  print('STAGE55_APPLIED='+json.dumps(applied),flush=True)
  data=parse_marker(execute(remote_stage(REMOTE_VERIFY,payload,prepared['backup'])),'STAGE55_DEPLOY')
  (evidence/'rollout.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
  print('STAGE55_DEPLOY='+json.dumps(data),flush=True)
 except Exception:
  # Includes timeout/connection loss after a potentially successful COMMIT.
  # The state remains actionable if recovery itself is interrupted.
  try:
   result=execute(rollback_remote(prepared['backup']))
   for line in result.splitlines():
    if line.startswith('STAGE55_'):print(line,flush=True)
   state.unlink()
  except Exception as recovery_error:
   print('STAGE55_RECOVERY_REQUIRED='+json.dumps({'backup':prepared['backup'],'reason':str(recovery_error)[-300:]}),flush=True)
  raise

if __name__=='__main__':main()
