#!/usr/bin/env python3
"""Guarded online feature-filter index with real slow-query plan validation and rollback."""
from stage52_disable_callback import execute

REMOTE = r'''set -eu
python3 - <<'PY'
import os,re,time,json,pathlib,subprocess,hashlib,shutil,datetime
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru')
idx='idx_megap_feature_value_prod_sku'
table='shop_product_features'
def emit(k,v):
 print('PERF72_'+k+'='+json.dumps(v,ensure_ascii=False),flush=True)
def run(cmd,script=None,timeout=35):
 p=subprocess.Popen(cmd,stdin=subprocess.PIPE if script is not None else None,
  stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 try:out,err=p.communicate(input=script,timeout=timeout)
 except subprocess.TimeoutExpired:
  p.kill();p.communicate();return (124,'')
 return p.returncode,out
php="$c=include '/home/web/vm-23f9aff9.na4u.ru/www/wa-config/db.php';echo $c['default']['database'];"
rc,name=run(['/usr/bin/php7.4','-r',php],timeout=10)
schema=name.strip()
if rc or re.fullmatch('[A-Za-z0-9_]+',schema) is None:raise RuntimeError('Active DB not found')
prefix='USE '+schema+';\n'
client=['mysql','--protocol=socket','-uroot','--connect-timeout=5','-N','-B']
def sql(q,timeout=40):
 return run(client,prefix+q,timeout)
def plan(q):
 rc,out=sql('EXPLAIN '+q,20)
 if rc:return None
 rows=[]
 for row in out.splitlines():
  v=row.split('\t')
  if len(v)>9:
   rows.append({'table':v[2],'type':v[3],'key':v[5],'rows':v[8],'extra':v[9][:100]})
 return rows
def countidx():
 rc,out=sql("SHOW INDEX FROM "+table+";",15)
 if rc:raise RuntimeError('Cannot read current indexes')
 return [row.split('\t') for row in out.splitlines()]
def hasidx():
 return any(len(x)>4 and x[2]==idx for x in countidx())
log=pathlib.Path('/var/log/mysql/mariadb-slow.log')
if not log.is_file():raise RuntimeError('Slow SQL log unavailable')
queries=[]
raw=log.read_text('utf8',errors='replace')[-420000:]
for block in re.split(r'(?m)^# Time:',raw):
 m=re.search('Query_time:\\s*([0-9.]+)',block)
 if not m:continue
 lines=block.splitlines()
 i=next((i for i,l in enumerate(lines) if l.startswith('SET timestamp=')),None)
 if i is None:continue
 q='\n'.join(lines[i+1:]).strip()
 if re.match(r'(?is)^SELECT\s',q) and all(x in q for x in ('shop_product','shop_product_features','shop_product_skus')) and q.count(';')==1 and q.endswith(';') and 'FOR UPDATE' not in q.upper():
  fingerprint=re.sub(r'\b\d+\b','?',q)
  fingerprint=' '.join(fingerprint.split())
  queries.append((float(m.group(1)),hashlib.sha256(fingerprint.encode('utf8')).hexdigest()[:12],q))
if not queries:raise RuntimeError('No real feature filter SQL found')
seen=set();chosen=[]
for seconds,digest,q in sorted(queries,reverse=True):
 if digest not in seen:
  seen.add(digest);chosen.append((seconds,digest,q))
 if len(chosen)>=3:break
original_plans=[plan(q) for sec,dig,q in chosen]
if not all(original_plans):raise RuntimeError('Baseline EXPLAIN failed')
if not any(any(x['table']=='p' and x['type']=='index' and int(x['rows'])>=50000 for x in p) for p in original_plans):
 raise RuntimeError('The expected large table scan no longer occurs')
if hasidx():
 emit('SKIPPED',{'reason':'Index already exists; no changes made'})
 raise SystemExit(0)
emit('PREFLIGHT',{'check':1,'ok':True,'plans':len(original_plans),'large_scan':True})
# Essential safety: enough free space, sufficient RAM, MariaDB 10.3 and light transaction pressure.
st=os.statvfs(str(root))
free=st.f_bavail*st.f_frsize
mem=pathlib.Path('/proc/meminfo').read_text()
avail=int(re.search(r'(?m)^MemAvailable:\s*(\d+)',mem).group(1))*1024
if free<8*1073741824 or avail<7*1073741824:
 raise RuntimeError('Not enough disk or memory for online index build')
rc,v=sql('SELECT VERSION();')
if rc or not v.startswith('10.3.23-MariaDB'):raise RuntimeError('Unexpected MariaDB version')
rc,x=sql('SELECT COUNT(*) FROM information_schema.innodb_trx;')
if rc or not x.strip().isdigit() or int(x.strip())>4:
 raise RuntimeError('Too many active transactions')
emit('PREFLIGHT',{'check':2,'ok':True,'disk_gb':round(free/1073741824,2),
 'available_ram_gb':round(avail/1073741824,2),'active_transactions':int(x.strip())})
# Keep original table DDL and exact index definitions. No customer data in backup.
folder=root/'storefront-backups'/('perf72-index-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ'))
folder.mkdir(parents=True,mode=0o700)
ddl=folder/'shop_product_features_schema.sql'
rc,body=run(['mysqldump','--protocol=socket','-uroot','--no-data','--skip-lock-tables',schema,table],timeout=23)
if rc or 'CREATE TABLE' not in body:raise RuntimeError('Schema backup failed')
ddl.write_text(body,encoding='utf8');os.chmod(str(ddl),0o600)
print('PERF72_BACKUP='+json.dumps({'schema_backup':str(ddl),'sha256':hashlib.sha256(body.encode('utf8')).hexdigest()}),flush=True)
built=False
try:
 # A low metadata lock wait prevents queueing import jobs while indexing.
 statement="SET SESSION lock_wait_timeout=5;\nALTER TABLE "+table+" ADD INDEX "+idx+" (feature_id,feature_value_id,product_id,sku_id), ALGORITHM=INPLACE, LOCK=NONE;\n"
 t0=time.time()
 rc,out=sql(statement,timeout=155)
 if rc:raise RuntimeError('MariaDB refused nonblocking index creation; no other changes requested')
 built=True
 if not hasidx():raise RuntimeError('New index not present')
 emit('INDEX_CREATED',{'ok':True,'seconds':round(time.time()-t0,2),'columns':['feature_id','feature_value_id','product_id','sku_id']})
 newplans=[plan(q) for sec,dig,q in chosen]
 if not all(newplans):raise RuntimeError('Post-index EXPLAIN failed')
 used=0
 for i,steps in enumerate(newplans):
  good=any(x['key']==idx for x in steps)
  if good:used+=1
  emit('PLAN',{'rank':i+1,'new_index_used':good,'steps':steps})
 if used==0:raise RuntimeError('Index is not chosen by actual slow queries; rolling back')
 routes=['/','/category/kompyuternye-kresla/','/category/kompyuternye-kresla/?page=2',
 '/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','/stul-prozrachnyy-pedrali-queen/',
 '/oplata/','/dostavka/','/o-kompanii/','/order/','/garantiya/']
 for i,path in enumerate(routes):
  rc,out=run(['curl','-ksSL','--max-time','22','--compressed','-w','\n%{http_code}','https://profikompany.ru'+path],timeout=27)
  head,sep,status=out.rpartition('\n')
  ok=rc==0 and status=='200' and '<title' in head and '+7 (812)' in head
  emit('TEST',{'test':i+1,'route':path,'ok':ok,'http':status})
  if not ok:raise RuntimeError('Storefront regression '+str(i+1))
 emit('DEPLOY',{'success':True,'index':idx,'used_in_plans':used,'plans':len(chosen),
  'checks':len(routes),'online':True,'catalog_data_modified':False})
except Exception as error:
 if built or hasidx():
  rc,out=sql('SET SESSION lock_wait_timeout=5;\nALTER TABLE '+table+' DROP INDEX '+idx+', ALGORITHM=INPLACE, LOCK=NONE;',timeout=100)
  emit('ROLLBACK',{'index_removed':rc==0 and not hasidx()})
 emit('ERROR',{'reason':str(error)[:220]})
 raise
PY
'''
if __name__=='__main__':
 result=execute(REMOTE)
 for line in result.splitlines():
  if line.startswith('PERF72_'):print(line,flush=True)
 if 'PERF72_DEPLOY=' not in result and 'PERF72_SKIPPED=' not in result:
  raise RuntimeError('No verified deployment result')
