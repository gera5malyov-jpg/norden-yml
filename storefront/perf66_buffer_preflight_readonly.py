#!/usr/bin/env python3
"""Read-only InnoDB sizing and dynamic resize readiness preflight."""
from stage52_disable_callback import execute
REMOTE=r'''set -eu
python3 - <<'PY'
import pathlib,subprocess,json,glob,re
def emit(k,v):print('PERF66_'+k+'='+json.dumps(v,ensure_ascii=False),flush=True)
for pattern in ['/etc/mysql/my.cnf','/etc/mysql/mariadb.conf.d/*.cnf','/etc/mysql/conf.d/*.cnf','/etc/my.cnf']:
 for name in glob.glob(pattern):
  try:txt=pathlib.Path(name).read_text('utf8',errors='replace')
  except:continue
  lines=[]
  for line in txt.splitlines():
   if not line.lstrip().startswith(('#',';')) and re.search(r'innodb_buffer_pool_size|innodb_buffer_pool_chunk_size|innodb_buffer_pool_instances',line,re.I):
    lines.append(line.strip()[:125])
  if lines:emit('CONFIG',{'file':name,'settings':lines})
php=r"""
$c=include '/home/web/vm-23f9aff9.na4u.ru/www/wa-config/db.php';
$db=is_array($c) && isset($c['default'])?$c['default']:array();
function say($x){echo "PERF66_DB=".json_encode($x,JSON_UNESCAPED_UNICODE)."\n";}
if(!isset($db['host'],$db['user'],$db['database'])){say(array('success'=>false,'reason'=>'db config incomplete'));exit;}
$p=tempnam('/tmp','perf66-r-');chmod($p,0600);
function qv($v){return '"'.str_replace(array("\\",'"',"\n","\r"),array("\\\\","\\\"","",""),(string)$v).'"';}
file_put_contents($p,"[client]\nhost=".qv($db['host'])."\nuser=".qv($db['user'])."\npassword=".qv(isset($db['password'])?$db['password']:'')."\n");
try{
 $queries=array("SELECT VERSION()",
  "SHOW GLOBAL VARIABLES WHERE Variable_name IN ('innodb_buffer_pool_size','innodb_buffer_pool_chunk_size','innodb_buffer_pool_instances','innodb_buffer_pool_resize_status','innodb_flush_method','innodb_file_per_table')",
  "SHOW GLOBAL STATUS WHERE Variable_name IN ('Innodb_buffer_pool_reads','Innodb_buffer_pool_read_requests','Innodb_buffer_pool_pages_free','Innodb_buffer_pool_pages_total','Innodb_buffer_pool_pages_data','Innodb_buffer_pool_wait_free','Innodb_data_reads','Created_tmp_disk_tables','Created_tmp_tables','Slow_queries','Threads_running')"
 );
 foreach($queries as $i=>$sql){
  $cmd='mysql --defaults-extra-file='.escapeshellarg($p).' -N -B '.escapeshellarg($db['database']).' -e '.escapeshellarg($sql).' 2>/dev/null';
  $rows=array();$code=1;exec($cmd,$rows,$code);
  say(array('id'=>$i,'ok'=>$code==0,'rows'=>array_slice($rows,0,30)));
 }
}finally{unlink($p);}
"""
p=subprocess.Popen(['/usr/bin/php7.4','-r',php],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
out,err=p.communicate(timeout=35)
for line in out.splitlines():
 if line.startswith('PERF66_DB='):print(line,flush=True)
emit('PHP_EXIT',{'code':p.returncode})
PY
'''
if __name__=='__main__':
 x=execute(REMOTE)
 for l in x.splitlines():
  if l.startswith('PERF66_'):print(l,flush=True)
 if 'PERF66_DB=' not in x:raise RuntimeError('Incomplete DB checks')
