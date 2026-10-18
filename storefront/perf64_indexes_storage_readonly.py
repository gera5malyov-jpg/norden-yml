#!/usr/bin/env python3
"""Read-only: protected storage tree, PHP runtime settings and MySQL indexes."""
from stage52_disable_callback import execute
REMOTE=r'''set -eu
python3 - <<'PY'
import pathlib,os,subprocess,json,re,datetime
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
def emit(key,d):print('PERF64_'+key+'='+json.dumps(d,ensure_ascii=False),flush=True)
def call(args,t=30):
 try:
  p=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  out,err=p.communicate(timeout=t)
  return out if p.returncode==0 else ''
 except Exception:return ''
for part in ['wa-data/protected/shop','wa-data/protected/shop/plugins','wa-data/protected/shop/0','wa-data/public/shop/plugins']:
 p=root/part
 if not p.is_dir():continue
 out=call(['du','-k','--max-depth=1',str(p)],t=55)
 rows=[]
 for line in out.splitlines():
  pair=line.split('\t',1)
  if len(pair)!=2:continue
  try:gb=round(int(pair[0])/1048576,2)
  except:continue
  rows.append((pair[1],gb))
 emit('STORAGE',{'path':part,'top':[{'name':pathlib.Path(x).name,'gb':n} for x,n in sorted(rows,key=lambda z:z[1],reverse=True)[:26]]})
for binary in ['php-cgi7.4','php7.4','php-cgi7.3']:
 text=call([binary,'-i'],t=12)
 if not text:continue
 keys=['opcache.enable','opcache.memory_consumption','opcache.validate_timestamps','opcache.file_update_protection','opcache.max_accelerated_files','memory_limit','max_execution_time','realpath_cache_size']
 vals={}
 for k in keys:
  match=re.search(r'(?m)^'+re.escape(k)+r'\s*=>\s*([^=\\n\\r]+)',text)
  if match:
   value=match.group(1).strip()
   if re.match(r'^(On|Off|[0-9]+|[0-9]+M|[0-9]+G)$',value): vals[k]=value
 emit('PHP_RUNTIME',{'binary':binary,'settings':vals})
# Query DB schema via PHP: load existing credentials server-side only, output metadata.
php=r"""
$c=include '/home/web/vm-23f9aff9.na4u.ru/www/wa-config/db.php';
$cfg=is_array($c) && isset($c['default'])?$c['default']:array();
function pr($x){echo 'PERF64_PHPDB='.json_encode($x,JSON_UNESCAPED_UNICODE)."\n";}
if(!extension_loaded('mysqli')){pr(array('ok'=>false,'reason'=>'mysqli not available'));exit;}
if(!isset($cfg['host'],$cfg['user'],$cfg['database'])){pr(array('ok'=>false,'reason'=>'db config incomplete'));exit;}
mysqli_report(MYSQLI_REPORT_OFF);
$db=@new mysqli($cfg['host'],$cfg['user'],isset($cfg['password'])?$cfg['password']:'',$cfg['database']);
if($db->connect_errno){pr(array('ok'=>false,'reason'=>'connection failed'));exit;}
$db->query("SET SESSION TRANSACTION READ ONLY");
$all=array();
$q=array(
 "SHOW GLOBAL STATUS WHERE Variable_name IN ('Slow_queries','Questions','Threads_running','Created_tmp_disk_tables','Created_tmp_tables','Select_full_join','Sort_merge_passes')",
 "SHOW GLOBAL VARIABLES WHERE Variable_name IN ('slow_query_log','long_query_time','innodb_buffer_pool_size','max_connections')",
 "SHOW INDEX FROM shop_product",
 "SHOW INDEX FROM shop_product_skus",
 "SHOW INDEX FROM shop_product_features",
 "SELECT table_name, table_rows, data_length, index_length FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name IN ('shop_product','shop_product_skus','shop_product_features')"
);
foreach($q as $n=>$query){
 $res=$db->query($query);
 if(!$res){$all[]=array('q'=>$n,'error'=>'permission or schema');continue;}
 $rows=array();
 while($r=$res->fetch_assoc()){
  if($n==2||$n==3||$n==4){$rows[]=array('index'=>$r['Key_name'],'column'=>$r['Column_name'],'seq'=>$r['Seq_in_index'],'unique'=>($r['Non_unique']=='0'));}
  elseif($n==5){$rows[]=array('table'=>$r['table_name'],'rows_estimate'=>$r['table_rows'],'data_mb'=>round($r['data_length']/1048576),'index_mb'=>round($r['index_length']/1048576));}
  else {$rows[]=array('name'=>$r['Variable_name'],'value'=>$r['Value']);}
 }
 $all[]=array('q'=>$n,'rows'=>$rows);
}
pr(array('ok'=>true,'diagnostics'=>$all));$db->close();
"""
for binary in ['/usr/bin/php7.4','/usr/bin/php']:
 p=call([binary,'-r',php],t=28)
 if p:
  for line in p.splitlines():
   if line.startswith('PERF64_PHPDB='):print(line,flush=True)
  break
PY
'''
if __name__=='__main__':
 x=execute(REMOTE)
 for l in x.splitlines():
  if l.startswith('PERF64_'):print(l,flush=True)
 if 'PERF64_STORAGE=' not in x:raise RuntimeError('Storage audit incomplete')
