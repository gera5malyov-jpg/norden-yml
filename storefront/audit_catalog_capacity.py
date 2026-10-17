#!/usr/bin/env python3
"""Readonly catalog sizing/host audit; never outputs credentials or customer data."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from stage52_disable_callback import execute
REMOTE=r"""set -eu
python3 - <<'PY'
import os,json
d={'loadavg':list(os.getloadavg())}
try:
 mem={}
 for row in open('/proc/meminfo'):
  if row.startswith(('MemTotal:','MemAvailable:','SwapTotal:','SwapFree:')):
   a=row.split();mem[a[0][:-1]+'_MiB']=round(float(a[1])/1024)
 d['memory']=mem
except Exception:pass
try:
 s=os.statvfs('/home/web/vm-23f9aff9.na4u.ru/www')
 d['disk']={'total_GiB':round(s.f_blocks*s.f_frsize/1073741824,1),'free_GiB':round(s.f_bavail*s.f_frsize/1073741824,1)}
except Exception:pass
print('CAPACITY_SERVER='+json.dumps(d),flush=True)
PY
dbfile=$(mktemp /tmp/shop-capacity-XXXXXX.php)
trap 'rm -f "$dbfile"' EXIT
cat > "$dbfile" <<'PHP'
<?php
error_reporting(E_ERROR);
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();$out=array('tables'=>array(),'indexes'=>array());
foreach(array('shop_product','shop_product_skus','shop_category','shop_product_features','shop_product_images') as $name){
 try{$r=$m->query('SELECT COUNT(*) AS n FROM '.$name)->fetchAssoc();$out['tables'][$name]=intval($r['n']);}
 catch(Throwable $e){$out['tables'][$name]='unavailable';}
}
try{$out['storage']=$m->query("SELECT TABLE_NAME,ENGINE,TABLE_ROWS,ROUND((DATA_LENGTH+INDEX_LENGTH)/1024/1024,1) AS size_mib FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('shop_product','shop_product_skus','shop_product_features','shop_product_images','shop_category') ORDER BY TABLE_NAME")->fetchAll();}
catch(Throwable $e){}
try{foreach($m->query('SHOW INDEX FROM shop_product')->fetchAll() as $x){$out['indexes'][]=array('index'=>$x['Key_name'],'column'=>$x['Column_name']);}}catch(Throwable $e){}
foreach(array('Threads_running','Slow_queries','Uptime') as $name){
 try{$s=$m->query("SHOW GLOBAL STATUS LIKE '".$name."'")->fetchAssoc();if($s)$out['status'][$name]=$s['Value'];}catch(Throwable $e){}
}
echo 'CAPACITY_DB='.json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
?>
PHP
chmod 644 "$dbfile"
su -s /bin/bash web -c "php $dbfile"
for suffix in '/' '/category/stoly_1/stoly-zhurnalnye/' '/barselona-kofeynyy-stolik-iz-iskusstvennogo-rotanga-tsvet-korichnevyy/'; do
  printf 'CAPACITY_HTTP='
  curl -ksSL --max-time 35 -o /dev/null -w "status=%{http_code};ttfb=%{time_starttransfer};total=%{time_total};bytes=%{size_download}" "https://profikompany.ru$suffix"
  printf '\n'
done
"""
def main():
 output=execute(REMOTE)
 seen=set()
 for line in output.splitlines():
  if line.startswith(('CAPACITY_SERVER=','CAPACITY_DB=','CAPACITY_HTTP=')):
   print(line,flush=True);seen.add(line.split('=',1)[0])
 if len(seen)<3:raise RuntimeError('Incomplete audit')
if __name__=='__main__':main()
