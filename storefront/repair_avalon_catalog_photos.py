#!/usr/bin/env python3
"""One-time fail-closed Avalon external-photo repair. No price, stock, name, category or SKU changes."""
from stage52_disable_callback import execute

REMOTE=r"""set -eu
python3 - <<'PY'
import base64,collections,concurrent.futures,datetime,hashlib,json,os,pathlib,re,shlex,subprocess,tempfile,urllib.parse,xml.etree.ElementTree as ET
ROOT=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
SITE='https://profikompany.ru'
URL_RE=re.compile(r'https?://[A-Za-z0-9/:._?&=%+-]+')
PHOTO='www.avalonmebel.ru/upload/'
PHP=r'''<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');$m=new waModel();
$mode=$argv[1];$tx=false;
try {
 if($mode==='inspect'){
  $rs=$m->query("SELECT id,name,url,summary,status FROM shop_product WHERE status=1 AND summary LIKE '%avalonmebel.ru/upload/%' LIMIT 2000")->fetchAll();
  echo 'AVALON_DB='.base64_encode(json_encode($rs,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)).PHP_EOL;exit(0);
 }
 $updates=json_decode(file_get_contents($argv[2]),true);
 if(!is_array($updates) || count($updates)<1 || count($updates)>520)throw new Exception('Invalid batch size');
 $m->query('START TRANSACTION');$tx=true;
 foreach($updates as $rec){
  $id=(int)$rec['id'];
  $cur=$m->query('SELECT id,name,url,summary,status FROM shop_product WHERE id=i:id FOR UPDATE',array('id'=>$id))->fetchAssoc();
  if(!$cur || (int)$cur['status']!==1 || $cur['name']!==$rec['name'] || $cur['url']!==$rec['url'])throw new Exception('Product identity changed '.$id);
  $from=$mode==='rollback'?$rec['after']:$rec['before'];
  $to=$mode==='rollback'?$rec['before']:$rec['after'];
  if($mode!=='apply' && $mode!=='rollback')throw new Exception('Invalid operation');
  if((string)$cur['summary']!==$from)throw new Exception('Source field changed '.$id);
  $m->query('UPDATE shop_product SET summary=s:replacement WHERE id=i:id AND summary=s:previous',
    array('replacement'=>$to,'previous'=>$from,'id'=>$id));
  $verify=$m->query('SELECT summary FROM shop_product WHERE id=i:id',array('id'=>$id))->fetchAssoc();
  if(!$verify || $verify['summary']!==$to)throw new Exception('Post-write mismatch '.$id);
 }
 $m->query('COMMIT');$tx=false;
 echo 'AVALON_DB_BATCH='.json_encode(array('mode'=>$mode,'updated'=>count($updates))).PHP_EOL;
}catch(Throwable $e){
 if($tx)$m->query('ROLLBACK');
 fwrite(STDERR,'AVALON_DB_GUARD: '.$e->getMessage().PHP_EOL);exit(1);
}
'''
def run(cmd,timeout=30):
 return subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=timeout)
with tempfile.NamedTemporaryFile('w',dir='/tmp',prefix='avalon-multi-',suffix='.php',delete=False) as tmp:
 tmp.write(PHP);phpfile=tmp.name
os.chmod(phpfile,0o644)
tempfiles=[]
def backend(mode,records=None):
 inpath=None
 if records is not None:
  fd,inpath=tempfile.mkstemp(prefix='avalon-plan-',suffix='.json',dir='/tmp')
  os.close(fd);tempfiles.append(inpath)
  with open(inpath,'w') as file:json.dump(records,file,ensure_ascii=False)
  os.chmod(inpath,0o644)
 cmd='php '+shlex.quote(phpfile)+' '+mode+((' '+shlex.quote(inpath)) if inpath else '')
 p=run(['su','-s','/bin/bash','web','-c',cmd],timeout=95)
 if p.returncode:raise RuntimeError('PHP '+mode+': '+p.stderr[-380:])
 marker='AVALON_DB=' if mode=='inspect' else 'AVALON_DB_BATCH='
 values=[line.split('=',1)[1] for line in p.stdout.splitlines() if line.startswith(marker)]
 if len(values)!=1:raise RuntimeError('PHP response missing '+mode)
 return json.loads(base64.b64decode(values[0]).decode('utf-8')) if mode=='inspect' else json.loads(values[0])
def namekey(name):return ' '.join((name or '').casefold().split())
def fetch(url,timeout=24):
 p=run(['curl','-ksSL','--max-time',str(timeout),'-w','\n__CODE__%{http_code}',url],timeout=timeout+5)
 if p.returncode or '\n__CODE__' not in p.stdout:raise RuntimeError('Public HTTP failed')
 body,status=p.stdout.rsplit('\n__CODE__',1)
 return body,status
try:
 records=backend('inspect')
 names=collections.Counter(namekey(r['name']) for r in records)
 if len(records)<400 or len(records)>750:raise RuntimeError('PREFLIGHT 1: unexpected supplier inventory')
 feed=run(['curl','-fsSL','--max-time','36','https://raw.githubusercontent.com/gera5malyov-jpg/norden-yml/main/avalon-podstolya/catalog.yml'],timeout=42)
 if feed.returncode:raise RuntimeError('PREFLIGHT 1: feed unreachable')
 offers=ET.fromstring(feed.stdout).findall('.//offer')
 matches=collections.defaultdict(list)
 for o in offers:
  matches[namekey(o.findtext('name'))].append([p.text for p in o.findall('picture') if p.text])
 eligible=[];skip=collections.Counter()
 for row in records:
  key=namekey(row['name']);options=matches.get(key,[])
  if names[key]!=1 or len(options)!=1:skip['ambiguous']+=1;continue
  old=str(row['summary'] or '');links=URL_RE.findall(old);pictures=options[0]
  if not old.startswith('[extimg]') or not links or len(links)!=len(pictures) or not all(PHOTO in u for u in links+pictures):
   skip['gallery_shape']+=1;continue
  if old.count('[extimg]')!=old.count('[/extimg]'):skip['markers']+=1;continue
  after=old;safe=True
  for a,b in zip(links,pictures):
   if after.count(a)!=1:safe=False;break
   after=after.replace(a,b,1)
  if not safe or URL_RE.findall(after)!=pictures:skip['content_ambiguous']+=1;continue
  if after==old:skip['unchanged']+=1;continue
  eligible.append({'id':int(row['id']),'name':row['name'],'url':row['url'],'before':old,'after':after,'pictures':pictures})
 print('AVALON_BULK_PREFLIGHT='+json.dumps({'check':1,'result':'passed','db_rows':len(records),
  'feed_offers':len(offers),'eligible':len(eligible),'skipped':dict(skip)},ensure_ascii=False),flush=True)
 if len(eligible)<350 or len(eligible)>520:raise RuntimeError('PREFLIGHT 1: bulk count unusual; no updates')
 # Preflight 2: verify every proposed new gallery link returns image content; unsafe products are excluded.
 photos=sorted(set(u for rec in eligible for u in rec['pictures']))
 def image_ok(url):
  p=run(['curl','-ksSL','-o','/dev/null','--max-time','14','-w','%{http_code} %{content_type}',url],timeout=17)
  return url,(p.returncode==0 and p.stdout.startswith('200 image/'))
 with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
  verified=dict(pool.map(image_ok,photos))
 eligible=[rec for rec in eligible if all(verified.get(u,False) for u in rec['pictures'])]
 print('AVALON_BULK_PREFLIGHT='+json.dumps({'check':2,'result':'passed','image_links_checked':len(photos),
  'image_links_ok':sum(verified.values()),'safe_products':len(eligible)},ensure_ascii=False),flush=True)
 if len(eligible)<1:raise RuntimeError('No photos passed preflight 2')
 # Preflight 3: check public product pages in a distributed pilot sample prior to writes.
 sample=eligible[:3]+eligible[len(eligible)//2:len(eligible)//2+3]+eligible[-4:]
 for row in sample:
  html,status=fetch(SITE+'/'+urllib.parse.quote(row['url'],safe='-._~/')+'/')
  expected_old=URL_RE.findall(row['before'])[0]
  if status!='200' or ('src="'+expected_old+'"') not in html:raise RuntimeError('PREFLIGHT 3 public site mismatch')
 print('AVALON_BULK_PREFLIGHT='+json.dumps({'check':3,'result':'passed','public_sample':len(sample)}),flush=True)
 eligible.sort(key=lambda r:r['id'])
 # Two separate transactions: a limited 20-product pilot, then remaining products.
 batches=[eligible[:20],eligible[20:]]
 summary={'batches':0,'updated':0,'tested':0}
 for index,batch in enumerate(batches,1):
  if not batch:continue
  folder=ROOT.parent/'storefront-backups'/('avalon-gallery-batch'+str(index)+'-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
  folder.mkdir(parents=True,mode=0o700)
  backups=folder/'summary-before-and-after.json'
  backups.write_text(json.dumps(batch,ensure_ascii=False))
  os.chmod(str(backups),0o600)
  applied=False
  try:
   result=backend('apply',batch);applied=True
   if result['updated']!=len(batch):raise RuntimeError('Invalid database transaction count')
   print('AVALON_BULK_APPLIED='+json.dumps({'batch':index,'count':len(batch),'backup':str(folder)}),flush=True)
   # Ten separate HTTP checks, spanning ten different updated products.
   ten=[batch[(j*(len(batch)-1))//9] for j in range(10)]
   for j,row in enumerate(ten,1):
    html,status=fetch(SITE+'/'+urllib.parse.quote(row['url'],safe='-._~/')+'/?avalon_verify='+str(os.getpid())+'_'+str(index)+'_'+str(j))
    old=URL_RE.findall(row['before'])[0];new=row['pictures'][0]
    ok=(status=='200' and ('src="'+new+'"') in html and ('src="'+old+'"') not in html
        and 'js-extimg-main-img' in html)
    print('AVALON_BULK_TEST='+json.dumps({'batch':index,'test':j,'ok':bool(ok),'http':status}),flush=True)
    if not ok:raise RuntimeError('Batch '+str(index)+' failed test '+str(j))
   summary['batches']+=1;summary['updated']+=len(batch);summary['tested']+=10
  except Exception as e:
   if applied:
    try:
     result=backend('rollback',batch)
     print('AVALON_BULK_ROLLBACK='+json.dumps({'batch':index,'restored':result['updated'],'backup':str(folder),'error':str(e)[:150]}),flush=True)
    except Exception as rollback_error:
     print('AVALON_BULK_ROLLBACK='+json.dumps({'batch':index,'restored':False,'backup':str(folder),'error':str(rollback_error)[:150]}),flush=True)
   raise
 print('AVALON_BULK_DONE='+json.dumps({'success':True,**summary,'skipped':dict(skip)},ensure_ascii=False),flush=True)
finally:
 os.unlink(phpfile)
 for f in tempfiles:
  try:os.unlink(f)
  except OSError:pass
PY
"""
if __name__=="__main__":
    result=execute(REMOTE)
    for line in result.splitlines():
        if line.startswith(("AVALON_BULK_PREFLIGHT=","AVALON_BULK_APPLIED=",
                            "AVALON_BULK_TEST=","AVALON_BULK_DONE=","AVALON_BULK_ROLLBACK=")):
            print(line,flush=True)
    if "AVALON_BULK_DONE=" not in result:
        raise RuntimeError("Missing completed guarded bulk report")
