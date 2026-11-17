#!/usr/bin/env python3
"""Restore the external photo gallery of Avalon 2224EM only.

Exact-identity guards, HTTPS image checks, private on-server backup,
transactional summary-only update, public smoke test and rollback.
Never downloads/stores image files in Webasyst or KIT.
"""
import json
from stage52_disable_callback import execute

REMOTE = r"""set -eu
python3 - <<'PY'
import base64,concurrent.futures,datetime,json,os,pathlib,re,shlex,subprocess,tempfile,time
ROOT=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
SLUG='podstole-2224em'
NAME='Подстолье 2224EM'
SOURCE='https://www.leomebel.ru/catalog/podstolya/podstole_2224em/'
NEW=[
 'https://www.leomebel.ru/upload/iblock/ff4/xsgpo7olsftxaiuo4jy479txrp9q3kno.jpg',
 'https://www.leomebel.ru/upload/iblock/ce0/osykpp9ihd8t6vxj2x8fuh58pd5wjhf2.jpg',
 'https://www.leomebel.ru/upload/iblock/14c/i7sppyvevw273troo8bc2w77u5m1jqeq.jpg',
 'https://www.leomebel.ru/upload/iblock/dc3/0zn2ihrhc5tc0e8ohz41furjn8vlff6p.jpg',
 'https://www.leomebel.ru/upload/iblock/3ea/hty0l2tygugzvihcnld5bdfn1r9i4jm4.jpg',
 'https://www.leomebel.ru/upload/iblock/b83/t8hbggecql3ebse0eadg16ydm6ns40na.jpg',
 'https://www.leomebel.ru/upload/iblock/68a/821ue5mmjeh4ny9jgd429spzer8a0hwi.jpg',
 'https://www.leomebel.ru/upload/iblock/1bb/zhk3ehk3qy31ima6ouktkdoblf83qn18.jpg',
 'https://www.leomebel.ru/upload/iblock/fdb/zakliy29ka0pui733cbz2riz48wuqj6k.jpg',
 'https://www.leomebel.ru/upload/iblock/1b9/4vp9sm0457izqd3a81ceyetqeaooz7vt.jpg'
]
PHP=r'''<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();
$mode=$argv[1];
$slug='podstole-2224em';
$name='Подстолье 2224EM';
if ($mode==='inspect') {
  $rows=$m->query('SELECT id,name,url,summary,status,image_id,price,count FROM shop_product WHERE url=s:url',
                 array('url'=>$slug))->fetchAll();
  if (count($rows)!==1) {fwrite(STDERR,"Product URL is not unique\n");exit(1);}
  $r=$rows[0];
  $photos=(int)$m->query('SELECT COUNT(*) FROM shop_product_images WHERE product_id=i:id',
                        array('id'=>(int)$r['id']))->fetchField();
  $r['summary_b64']=base64_encode((string)$r['summary']);
  $r['native_photo_count']=$photos;
  unset($r['summary']);
  echo 'AVALON_STATE='.json_encode($r,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
  exit(0);
}
if (!in_array($mode,array('apply','rollback'),true)) {fwrite(STDERR,"Invalid mode\n");exit(1);}
$plan=json_decode(file_get_contents($argv[2]),true);
if (!is_array($plan) || empty($plan['id']) || !isset($plan['before'],$plan['after'])) {
  fwrite(STDERR,"Invalid plan\n");exit(1);
}
$from=$mode==='apply'?$plan['before']:$plan['after'];
$to=$mode==='apply'?$plan['after']:$plan['before'];
$tx=false;
try {
  $m->query('START TRANSACTION');$tx=true;
  $r=$m->query('SELECT id,name,url,summary,status FROM shop_product WHERE id=i:id FOR UPDATE',
               array('id'=>(int)$plan['id']))->fetchAssoc();
  if (!$r || $r['url']!==$slug || $r['name']!==$name || (int)$r['status']!==1 ||
      (string)$r['summary']!==$from)throw new Exception('Identity or summary changed');
  $m->query('UPDATE shop_product SET summary=s:new WHERE id=i:id AND summary=s:old',
            array('new'=>$to,'id'=>(int)$plan['id'],'old'=>$from));
  $v=$m->query('SELECT summary FROM shop_product WHERE id=i:id',
               array('id'=>(int)$plan['id']))->fetchAssoc();
  if (!$v || (string)$v['summary']!==$to)throw new Exception('Verification mismatch');
  $m->query('COMMIT');$tx=false;
  echo 'AVALON_WRITE='.json_encode(array('operation'=>$mode,'product_id'=>(int)$plan['id']))."\n";
} catch(Throwable $e) {
  if($tx)$m->query('ROLLBACK');
  fwrite(STDERR,'GUARD: '.$e->getMessage()."\n");
  exit(1);
}
'''
def run(args,timeout=24):
    return subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=timeout)
with tempfile.NamedTemporaryFile('w',encoding='utf-8',prefix='avalon2224-',suffix='.php',dir='/tmp',delete=False) as f:
    f.write(PHP);phpfile=f.name
os.chmod(phpfile,0o644)
planfile=None
backup=None
applied=False
def php(mode):
    cmd='php '+shlex.quote(phpfile)+' '+mode
    if mode!='inspect':cmd+=' '+shlex.quote(planfile)
    p=run(['su','-s','/bin/bash','web','-c',cmd],timeout=35)
    if p.returncode:raise RuntimeError('Webasyst DB '+mode+': '+p.stderr[-500:])
    prefix='AVALON_STATE=' if mode=='inspect' else 'AVALON_WRITE='
    vals=[line[len(prefix):] for line in p.stdout.splitlines() if line.startswith(prefix)]
    if len(vals)!=1:raise RuntimeError('Unexpected database response '+mode)
    return json.loads(vals[0])
def get_html(url):
    p=run(['curl','-ksSL','--max-time','22','-w','\n__STATUS__%{http_code}',url],timeout=26)
    if p.returncode or '\n__STATUS__' not in p.stdout:raise RuntimeError('Site unreachable')
    return p.stdout.rsplit('\n__STATUS__',1)
def image_check(url):
    p=run(['curl','-ksSL','--max-time','16','-o','/dev/null','-H',
           'Referer: https://profikompany.ru/podstole-2224em/',
           '-w','%{http_code} %{content_type} %{size_download}',url],timeout=20)
    parts=p.stdout.strip().split()
    return p.returncode==0 and len(parts)==3 and parts[0]=='200' and parts[1]=='image/jpeg' and int(float(parts[2]))>10000
try:
    lint=run(['php','-l',phpfile],timeout=10)
    if lint.returncode:raise RuntimeError('PHP syntax error: '+lint.stdout+lint.stderr)
    before=php('inspect')
    if (before['name']!=NAME or before['url']!=SLUG or int(before['status'])!=1
        or int(before['native_photo_count'])!=0):
        raise RuntimeError('Product is not an active external-image-only exact match')
    old=base64.b64decode(before['summary_b64']).decode('utf-8')
    block=re.compile(r'\[extimg\](.*?)\[/extimg\]',re.I|re.S)
    matches=list(block.finditer(old))
    old_urls=[]
    for m in matches:
        old_urls.extend(re.findall(r'https?://[^\s<\]]+',m.group(1)))
    if (not old.lstrip().startswith('[extimg]') or not 8<=len(old_urls)<=35
        or not all('avalonmebel.ru/upload/' in u for u in old_urls)
        or 'https://www.avalonmebel.ru/upload/iblock/0e1/0e1b92dad14ba0eed237f110f2a3ec68.jpg' not in old_urls):
        raise RuntimeError('Existing extimg gallery differs from verified example')
    old_html,old_status=get_html('https://profikompany.ru/'+SLUG+'/')
    if (old_status!='200' or 'src="'+old_urls[0]+'"' not in old_html
        or 'product-gallery--extimg' not in old_html):
        raise RuntimeError('Public gallery does not match its stored URL')
    source_html,source_status=get_html(SOURCE)
    if source_status!='200' or NAME not in source_html:
        raise RuntimeError('Exact alternative photo source unavailable')
    if not all('/upload/iblock/' in url and url.split('www.leomebel.ru',1)[1] in source_html for url in NEW):
        raise RuntimeError('Replacement images not present in the source product gallery')
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        valid=list(pool.map(image_check,NEW))
    if not all(valid):raise RuntimeError('Some replacement images are not publicly available')
    print('AVALON_2224_PREFLIGHT='+json.dumps({'product_id':int(before['id']),
         'old_links':len(old_urls),'new_links':len(NEW),'all_images_http_200':True}),flush=True)
    new_lines=''.join('[extimg]'+u+'[/extimg]\n' for u in NEW)
    n=[0]
    def replace(m):
        n[0]+=1
        return new_lines if n[0]==1 else ''
    after=block.sub(replace,old)
    if n[0]!=len(matches) or old==after or after.count('[extimg]')!=len(NEW):
        raise RuntimeError('Unexpected summary rewrite')
    folder=ROOT.parent/'storefront-backups'/('avalon2224-photo-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
    folder.mkdir(parents=True,mode=0o700)
    backup=folder/'summary-before.txt'
    backup.write_text(old,encoding='utf-8');os.chmod(str(backup),0o600)
    plan={'id':int(before['id']),'before':old,'after':after}
    fd,planfile=tempfile.mkstemp(prefix='avalon2224-plan-',suffix='.json',dir='/tmp')
    with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(plan,f,ensure_ascii=False)
    os.chmod(planfile,0o600)
    # The PHP process runs as 'web', so permit it to read only this temporary plan.
    os.chmod(planfile,0o644)
    result=php('apply');applied=True
    print('AVALON_2224_APPLIED='+json.dumps({'product_id':result['product_id'],
                                           'backup':str(backup)}),flush=True)
    verified=False
    for attempt in range(1,6):
        h,status=get_html('https://profikompany.ru/'+SLUG+'/?imgcheck='+str(os.getpid())+'_'+str(attempt))
        verified=(status=='200' and 'product-gallery--extimg' in h
                  and 'src="'+NEW[0]+'"' in h and 'src="'+old_urls[0]+'"' not in h
                  and all(u in h for u in NEW))
        print('AVALON_2224_SMOKE='+json.dumps({'attempt':attempt,'ok':verified,'http':status}),flush=True)
        if verified:break
        time.sleep(2)
    if not verified:raise RuntimeError('Public storefront did not show all replacement photos')
    current=php('inspect')
    if (current['summary_b64']!=base64.b64encode(after.encode('utf-8')).decode('ascii')
        or current['price']!=before['price'] or current['count']!=before['count']
        or current['status']!=before['status'] or current['image_id']!=before['image_id']):
        raise RuntimeError('Post-write database field check failed')
    print('AVALON_2224_DONE='+json.dumps({'ok':True,'product_id':int(before['id']),
          'visible_external_photos':len(NEW),'backup':str(backup)}),flush=True)
except Exception as e:
    if applied and planfile:
        try:
            rolled=php('rollback')
            print('AVALON_2224_ROLLBACK='+json.dumps({'restored':True,
                'product_id':rolled['product_id'],'reason':str(e)[:170]}),flush=True)
        except Exception as error:
            print('AVALON_2224_ROLLBACK='+json.dumps({'restored':False,
              'backup':str(backup),'reason':str(error)[:170]}),flush=True)
    raise
finally:
    try:os.unlink(phpfile)
    except OSError:pass
    if planfile:
        try:os.unlink(planfile)
        except OSError:pass
PY
"""
if __name__=="__main__":
    output=execute(REMOTE)
    for line in output.splitlines():
        if line.startswith(('AVALON_2224_PREFLIGHT=','AVALON_2224_APPLIED=',
                            'AVALON_2224_SMOKE=','AVALON_2224_DONE=',
                            'AVALON_2224_ROLLBACK=')):
            print(line,flush=True)
    if 'AVALON_2224_DONE=' not in output:
        raise RuntimeError('Photo restore did not finish')
