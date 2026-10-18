#!/usr/bin/env python3
"""Read-only EXPLAIN of representative real slow catalog SELECTs; never log SQL values."""
from stage52_disable_callback import execute

REMOTE = r'''set -eu
python3 - <<'PY'
import pathlib,subprocess,re,hashlib,collections,json
def emit(k,v):
 print('PERF69_'+k+'='+json.dumps(v,ensure_ascii=False),flush=True)
def sqlrun(script,limit=25):
 p=subprocess.Popen(['mysql','--protocol=socket','-uroot','--connect-timeout=5','-N','-B'],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    universal_newlines=True)
 try:
  out,err=p.communicate(input=script,timeout=limit)
 except subprocess.TimeoutExpired:
  p.kill();p.communicate();return None
 return out if p.returncode==0 else None
# Several databases may contain shop tables (e.g. backups). Read the active Webasyst schema from server-local PHP config.
php_code="$c=include '/home/web/vm-23f9aff9.na4u.ru/www/wa-config/db.php';echo isset($c['default']['database'])?$c['default']['database']:'';"
p=subprocess.Popen(['/usr/bin/php7.4','-r',php_code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
value,errors=p.communicate(timeout=8)
schema=value.strip()
if p.returncode!=0 or re.fullmatch(r'[A-Za-z0-9_]+',schema) is None:
 raise RuntimeError('Unable to identify active Webasyst database')
prefix='USE '+schema+';\n'
emit('SCHEMA',{'active_config_selected':True})
log=pathlib.Path('/var/log/mysql/mariadb-slow.log')
if not log.is_file():raise RuntimeError('No slow log')
raw=log.read_text('utf8',errors='replace')[-420000:]
queries=[]
for block in re.split(r'(?m)^# Time:',raw):
 m=re.search(r'Query_time:\s*([0-9.]+)',block)
 if not m:continue
 lines=block.splitlines()
 start=next((i for i,x in enumerate(lines) if x.startswith('SET timestamp=')),None)
 if start is None:continue
 query='\n'.join(lines[start+1:]).strip()
 if not re.match(r'(?is)^SELECT\s',query):continue
 if not all(x in query.lower() for x in ('shop_product','shop_product_skus','shop_product_features')):continue
 if query.count(';')!=1 or not query.endswith(';'):continue
 if 'INTO OUTFILE' in query.upper() or 'FOR UPDATE' in query.upper():continue
 normalized=re.sub(r"'(?:''|[^'])*'","?",query)
 normalized=re.sub(r'\b\d+\b','?',normalized)
 normalized=' '.join(normalized.lower().split())
 digest=hashlib.sha256(normalized.encode('utf8')).hexdigest()[:12]
 queries.append((float(m.group(1)),digest,query))
if not queries:raise RuntimeError('No catalog slow SELECTs found')
emit('PRECHECK',{'slow_selects':len(queries),'patterns':len(set(x[1] for x in queries))})
seen=set()
for sec,hashcode,query in sorted(queries,reverse=True):
 if hashcode in seen:continue
 seen.add(hashcode)
 # Query skeleton contains no original literals; inspect join predicates and sort clauses only.
 skeleton=re.sub(r"'(?:''|[^'])*'","?",query)
 skeleton=re.sub(r'\b\d+\b','?',skeleton)
 skeleton=' '.join(skeleton.split())
 emit('QUERY_SHAPE',{'rank':len(seen),'template':skeleton[:900]})
 result=sqlrun(prefix+'EXPLAIN '+query,22)
 if result is None:
  emit('EXPLAIN',{'rank':len(seen),'pattern':hashcode,'slow_seconds':round(sec,3),'available':False})
 else:
  rows=[]
  for line in result.splitlines():
   parts=line.split('\t')
   if len(parts)<10:continue
   rows.append({'table':parts[2][:60],'access':parts[3][:30],'possible':parts[4][:120],
    'key':parts[5][:90],'estimated_rows':parts[8][:30],'extra':parts[9][:180]})
  emit('EXPLAIN',{'rank':len(seen),'pattern':hashcode,'slow_seconds':round(sec,3),'available':True,'steps':rows[:10]})
 if len(seen)==3:break
for table in ('shop_product','shop_product_skus','shop_product_features'):
 result=sqlrun(prefix+'SHOW INDEX FROM '+table+';',15)
 if result is None:continue
 indexes=collections.defaultdict(list)
 for line in result.splitlines():
  v=line.split('\t')
  if len(v)<7:continue
  try:order=int(v[3])
  except ValueError:continue
  indexes[v[2]].append((order,v[4]))
 emit('INDEX',{'table':table,'columns':{key:[x[1] for x in sorted(val)] for key,val in indexes.items()}})
PY
'''
if __name__ == '__main__':
 out=execute(REMOTE)
 for line in out.splitlines():
  if line.startswith('PERF69_'):print(line,flush=True)
 if 'PERF69_EXPLAIN=' not in out:
  raise RuntimeError('No SQL plans could be verified')
