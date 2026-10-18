#!/usr/bin/env python3
"""Safe read-only disk and slow query fingerprint audit. Never print raw SQL or credentials."""
from stage52_disable_callback import execute
REMOTE=r'''set -eu
python3 - <<'PY'
import os,re,json,pathlib,subprocess,collections,hashlib,glob
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
def emit(topic,info):print('PERF62_'+topic+'='+json.dumps(info,ensure_ascii=False),flush=True)
def cmd(args,timeout=20):
 try:
  p=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  out,err=p.communicate(timeout=timeout)
  return out if p.returncode==0 else ''
 except Exception:return ''
for part in ['/home/web','/home/web/vm-23f9aff9.na4u.ru/www/wa-cache','/home/web/vm-23f9aff9.na4u.ru/www/wa-data/public','/home/web/vm-23f9aff9.na4u.ru/www/wa-log','/home/web/vm-23f9aff9.na4u.ru/www/wa-apps','/home/web/vm-23f9aff9.na4u.ru/www/wa-data','/var/lib/mysql','/var/log/mysql','/var/cache','/tmp']:
 size=cmd(['du','-sk',part],timeout=29).strip().split('\t')[0]
 try:mb=round(int(size)/1024) 
 except:mb=None
 emit('DISK',{'target':part.replace('/home/web/vm-23f9aff9.na4u.ru/www','SITE'),'mb':mb})
# MariaDB status via local socket only; no credential files read aloud.
for q in ["SHOW GLOBAL STATUS WHERE Variable_name IN ('Slow_queries','Questions','Threads_running','Created_tmp_disk_tables','Created_tmp_tables','Select_full_join','Sort_merge_passes')",
          "SHOW GLOBAL VARIABLES WHERE Variable_name IN ('slow_query_log','long_query_time','innodb_buffer_pool_size','max_connections')"]:
 out=cmd(['mysql','--no-defaults','-N','-B','-e',q],timeout=7)
 data=[]
 for row in out.splitlines():
  vals=row.split('\t')
  if len(vals)==2 and re.match('^[A-Za-z_]+$',vals[0]) and re.match('^[0-9.]+$|^(ON|OFF)$',vals[1]):
   data.append({'key':vals[0],'value':vals[1]})
 emit('SQL_STATUS',{'accessible':bool(data),'items':data})
# Safe aggregate slow query log - never output SQL statements, PII or literals.
log=pathlib.Path('/var/log/mysql/mariadb-slow.log')
if log.is_file():
 raw=log.read_text('utf-8',errors='replace')[-220000:]
 blocks=re.split(r'(?m)^# Time:',raw)
 stats=[]
 for block in blocks:
  match=re.search(r'Query_time:\s*([0-9.]+).*?Lock_time:\s*([0-9.]+)',block,re.S)
  if not match:continue
  try:time=float(match.group(1)); lock=float(match.group(2))
  except:continue
  sql=re.split(r'(?m)^SET timestamp=\d+;',block)
  query=sql[-1].strip() if len(sql)>1 else ''
  if not query or query.startswith('#'):continue
  tables=sorted(set(x.lower() for x in re.findall(r'\b(?:from|join|update|into)\s+[\x60]?([a-zA-Z_][a-zA-Z_0-9]*)',query,re.I)))
  operation=re.search(r'\b(SELECT|UPDATE|INSERT|DELETE|REPLACE|ALTER|CREATE)\b',query,re.I)
  stats.append({'duration':round(time,3),'lock':round(lock,3),'operation':operation.group(1).upper() if operation else 'OTHER',
   'tables':tables[:7],'joins':len(re.findall(r'\bjoin\b',query,re.I)), 'group_by':bool(re.search(r'group\s+by',query,re.I)),
   'sorting':bool(re.search('order\\s+by',query,re.I)),
   'temporary':bool(re.search('temporary|filesort',block,re.I)),
   'length':len(query)})
 operations=collections.Counter(x['operation'] for x in stats)
 bytable=collections.Counter(t for x in stats for t in x['tables'])
 biggest=sorted(stats,key=lambda x:x['duration'],reverse=True)[:25]
 emit('SLOW_SUMMARY',{'file_size_kb':round(log.stat().st_size/1024),'seen':len(stats),
  'count_by_query_type':dict(operations),'top_tables':bytable.most_common(25),
  'slowest':biggest})
# Exact PHP opcache status and main application config, without secrets.
for executable in ['/usr/bin/php','/usr/bin/php7.4']:
 output=cmd([executable,'-r',"echo PHP_VERSION.'|'.ini_get('opcache.enable').'|'.ini_get('opcache.enable_cli').'|'.ini_get('opcache.memory_consumption').'|'.ini_get('opcache.validate_timestamps');"],timeout=7).strip()
 if output:
  vals=output.split('|')
  emit('PHP_CLI',{'binary':executable,'version':vals[0],'opcache_enabled':vals[1] if len(vals)>1 else None,
    'opcache_cli':vals[2] if len(vals)>2 else None,'memory_mb':vals[3] if len(vals)>3 else None,
    'timestamp_validation':vals[4] if len(vals)>4 else None})
PY
'''
if __name__=='__main__':
 result=execute(REMOTE)
 for ln in result.splitlines():
  if ln.startswith('PERF62_'):print(ln,flush=True)
 if 'PERF62_SLOW_SUMMARY=' not in result:
  raise RuntimeError('Incomplete slow SQL audit')
