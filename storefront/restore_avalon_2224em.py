#!/usr/bin/env python3
"""Remove the unauthorized third-party images from Avalon 2224EM.
Restore precisely the official-supplier summary saved before the prior edit.
Safely guarded; no prices, stocks or other fields affected.
"""
from stage52_disable_callback import execute
REMOTE=r"""set -eu
python3 - <<'PY'
import base64,datetime,json,os,pathlib,re,shlex,subprocess,tempfile
ROOT=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
BACK=ROOT.parent/'storefront-backups/avalon2224-photo-20261117T075404Z-88992/summary-before.txt'
if not BACK.is_file():raise RuntimeError('Pre-edit summary backup not found')
original=BACK.read_text(encoding='utf-8')
if original.count('www.avalonmebel.ru/upload/iblock/')!=12 or 'www.leomebel.ru' in original:
    raise RuntimeError('Original backup image-source guard failed')
PHP=r'''<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$m=new waModel();$tx=false;
$old=file_get_contents($argv[1]);if($old===false)throw new Exception('Plan unavailable');
try {
$m->query('START TRANSACTION');$tx=true;
$r=$m->query('SELECT id,name,url,status,summary FROM shop_product WHERE id=i:id FOR UPDATE',
array('id'=>1476064))->fetchAssoc();
if(!$r || $r['name']!=='Подстолье 2224EM' || $r['url']!=='podstole-2224em' || (int)$r['status']!==1)throw new Exception('Product identity changed');
$now=(string)$r['summary'];
if ($now===$old) {
 $m->query('COMMIT');$tx=false;
 echo 'AVALON_REVERT='.json_encode(array('ok'=>true,'already_reverted'=>true,'product_id'=>1476064))."\n";
 exit(0);
}
if (substr_count($now,'www.leomebel.ru/upload/iblock/')!==10 || strpos($now,'www.avalonmebel.ru/upload/')!==false ||
 substr_count($now,'[extimg]')!==10 || substr_count($now,'[/extimg]')!==10)throw new Exception('Existing live third-party gallery differs from expected');
$strip=function($v){return preg_replace('/\s+/u',' ',preg_replace('/\[extimg\].*?\[\/extimg\]/is','',$v));};
if($strip($now)!==$strip($old))throw new Exception('Non-image summary content has changed');
$m->query('UPDATE shop_product SET summary=s:old WHERE id=i:id AND summary=s:current',
array('old'=>$old,'id'=>1476064,'current'=>$now));
$check=$m->query('SELECT summary FROM shop_product WHERE id=i:id',array('id'=>1476064))->fetchAssoc();
if(!$check || $check['summary']!==$old)throw new Exception('Update verification failed');
$m->query('COMMIT');$tx=false;
echo 'AVALON_REVERT='.json_encode(array('ok'=>true,'already_reverted'=>false,'product_id'=>1476064,'restored_official_urls'=>12))."\n";
} catch(Throwable $e) {
if($tx)$m->query('ROLLBACK');
fwrite(STDERR,'REVERT_GUARD: '.$e->getMessage()."\n");exit(1);
}
'''
with tempfile.NamedTemporaryFile('w',suffix='.php',prefix='avalon-revert-',dir='/tmp',delete=False) as fp:
    fp.write(PHP);php=fp.name
with tempfile.NamedTemporaryFile('w',suffix='.txt',prefix='avalon-orig-',dir='/tmp',delete=False) as fp:
    fp.write(original);plan=fp.name
os.chmod(php,0o644);os.chmod(plan,0o644)
try:
    cmd='php '+shlex.quote(php)+' '+shlex.quote(plan)
    proc=subprocess.Popen(['su','-s','/bin/bash','web','-c',cmd],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
    out,err=proc.communicate()
    if proc.returncode!=0:raise RuntimeError('Revert failed: '+err[-500:])
    if 'AVALON_REVERT=' not in out:raise RuntimeError('No database confirmation')
    print(out.strip(),flush=True)
    import urllib.request,ssl
    url='https://profikompany.ru/podstole-2224em/?source_revert=20261117'
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
    try:
        html=urllib.request.urlopen(req,context=ssl._create_unverified_context(),timeout=22).read().decode('utf-8','replace')
        verified='www.leomebel.ru/upload/iblock/' not in html and 'www.avalonmebel.ru/upload/iblock/' in html
        print('AVALON_REVERT_PUBLIC='+json.dumps({'ok':verified,'third_party_urls_found':not verified}),flush=True)
        if not verified:raise RuntimeError('Public page still includes unwanted third-party URLs')
    except Exception as e:
        raise RuntimeError('Reverted database but public smoke test failed: '+str(e))
finally:
    os.unlink(php);os.unlink(plan)
PY
"""
if __name__=="__main__":
    result=execute(REMOTE)
    for line in result.splitlines():
        if line.startswith(('AVALON_REVERT=','AVALON_REVERT_PUBLIC=')):
            print(line,flush=True)
    if 'AVALON_REVERT_PUBLIC=' not in result:raise RuntimeError('Missing public verification')
