#!/usr/bin/env python3
"""Server-side read-only index inspection using transient MySQL option file, deleted on exit."""
from stage52_disable_callback import execute
REMOTE=r'''set -eu
python3 - <<'PY'
import json,subprocess,pathlib
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
def emit(k,x):print('PERF65_'+k+'='+json.dumps(x,ensure_ascii=False),flush=True)
f=root/'wa-data/public/shop/themes/pureMegapolis42/home.html'
if f.is_file():
 s=f.read_text('utf8',errors='replace').splitlines()
 emit('HOME_SOURCE',{'lines':[{'line':i+1,'code':s[i][:500]} for i in range(min(85,len(s))) if 23<=i+1<=67]})
php=r"""
$c=include '/home/web/vm-23f9aff9.na4u.ru/www/wa-config/db.php';
$db=is_array($c) && isset($c['default'])?$c['default']:array();
function show($x){echo "PERF65_DB=".json_encode($x,JSON_UNESCAPED_UNICODE)."\n";}
show(array('extension_pdo_mysql'=>extension_loaded('pdo_mysql'),'extension_mysqli'=>extension_loaded('mysqli'),'credential_fields_present'=>isset($db['host'],$db['user'],$db['database'])));
if(!isset($db['host'],$db['user'],$db['database']))exit;
$p=tempnam('/tmp','schema-r-');
if(!$p){show(array('error'=>'cannot create temporary access file'));exit;}
chmod($p,0600);
function configvalue($v){return '"'.str_replace(array("\\",'"',"\n","\r"),array("\\\\","\\\"","",""),(string)$v).'"';}
$cfg="[client]\nhost=".configvalue($db['host'])."\nuser=".configvalue($db['user'])."\npassword=".configvalue(isset($db['password'])?$db['password']:'')."\n";
if(isset($db['port']) && $db['port'])$cfg.="port=".intval($db['port'])."\n";
file_put_contents($p,$cfg);
try {
 $queries=array(
  'SHOW INDEX FROM shop_product',
  'SHOW INDEX FROM shop_product_skus',
  'SHOW INDEX FROM shop_product_features',
  "SHOW GLOBAL STATUS WHERE Variable_name IN ('Slow_queries','Questions','Threads_running','Created_tmp_disk_tables','Created_tmp_tables','Select_full_join','Sort_merge_passes')",
  "SHOW GLOBAL VARIABLES WHERE Variable_name IN ('slow_query_log','long_query_time','innodb_buffer_pool_size','max_connections')",
  "SELECT TABLE_NAME,TABLE_ROWS,DATA_LENGTH,INDEX_LENGTH FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name IN ('shop_product','shop_product_skus','shop_product_features')"
 );
 foreach($queries as $i=>$query){
  $args='mysql --defaults-extra-file='.escapeshellarg($p).' -N -B '.escapeshellarg($db['database']).' -e '.escapeshellarg($query).' 2>/dev/null';
  $output=array();$status=1;
  exec($args,$output,$status);
  if($status!=0){show(array('query_id'=>$i,'available'=>false));continue;}
  $rows=array();
  foreach($output as $line){
   $parts=explode("\t",$line);
   if($i<=2 && count($parts)>4){$rows[]=array('index'=>$parts[2],'seq'=>$parts[3],'column'=>$parts[4],'not_unique'=>($parts[1]=='1'));}
   elseif($i>=3 && $i<=4 && count($parts)>1){$rows[]=array('key'=>$parts[0],'value'=>$parts[1]);}
   elseif($i==5 && count($parts)>3){$rows[]=array('table'=>$parts[0],'approx_rows'=>$parts[1],'data_mb'=>round(intval($parts[2])/1048576),'index_mb'=>round(intval($parts[3])/1048576));}
  }
  show(array('query_id'=>$i,'available'=>true,'rows'=>$rows));
 }
} finally{if(file_exists($p))unlink($p);}
"""
p=subprocess.Popen(['/usr/bin/php7.4','-r',php],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
out,err=p.communicate(timeout=39)
for line in out.splitlines():
 if line.startswith('PERF65_DB='):print(line,flush=True)
emit('DB_PROBE',{'php_exit':p.returncode,'output_lines':len(out.splitlines()),'stderr_type':'error' if err else 'none'})
PY
'''
if __name__=='__main__':
 output=execute(REMOTE)
 for l in output.splitlines():
  if l.startswith('PERF65_'):print(l,flush=True)
 if 'PERF65_DB_PROBE=' not in output:raise RuntimeError('No verified read-only result')
