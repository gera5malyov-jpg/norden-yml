#!/usr/bin/env python3
"""Read-only SQL execution-plan audit of representative slow catalog filters.

No SQL text, literals, customer data or credentials leave the server. Only sanitized
index choices and estimated plan sizes appear in logs.
"""
from stage52_disable_callback import execute

REMOTE=r'''set -eu
python3 - <<'PY'
import pathlib,re,subprocess,json,collections,hashlib
path=pathlib.Path('/var/log/mysql/mariadb-slow.log')
def emit(kind,obj):
 print('PERF69_'+kind+'='+json.dumps(obj,ensure_ascii=False),flush=True)
if not path.is_file():raise RuntimeError('Slow query log does not exist')
raw=path.read_text('utf8',errors='replace')[-390000:]
samples=[]
for blk in re.split(r'(?m)^# Time:',raw):
 m=re.search(r'Query_time:\s*([0-9.]+)',blk)
 if not m:continue
 lines=blk.splitlines()
 at=next((i for i,x in enumerate(lines) if x.startswith('SET timestamp=')),None)
 if at is None:continue
 sql='\n'.join(lines[at+1:]).strip()
 if not re.match(r'(?is)^SELECT\s',sql):continue
 if not all(x in sql.lower() for x in ('shop_product_features','shop_product_skus','shop_product')):continue
 if not sql.endswith(';') or sql.count(';')!=1:continue
 if 'INTO OUTFILE' in sql.upper() or 'FOR UPDATE' in sql.upper():continue
 duration=float(m.group(1))
 # Deduplicate SQL shapes by replacing constants, then inspect three most expensive.
 normalized=re.sub(r"'(?:''|[^'])*'","?",sql)
 normalized=re.sub(r'\b\d+\b','?',normalized)
 normalized=' '.join(normalized.lower().split())
 fingerprint=hashlib.sha256(normalized.encode('utf8')).hexdigest()[:16]
 samples.append((duration,fingerprint,sql))
selected=[]
seen=set()
for duration,fingerprint,sql in sorted(samples,reverse=True):
 if fingerprint in seen:continue
 seen.add(fingerprint);selected.append((duration,fingerprint,sql))
 if len(selected)==4:break
emit('PREFLIGHT',{'matched_queries':len(samples),'distinct_patterns':len(set(x[1] for x in samples)),'selected':len(selected)})
if not selected:raise RuntimeError('No matching slow SELECT patterns to inspect')
def sqlrun(stmt,timeout=20):
 # SQL goes through standard input, not process arguments or logs.
 args=['mysql','--protocol=socket','-uroot','--connect-timeout=5','-N','-B']
 p=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 try:out,err=p.communicate(input=stmt,timeout=timeout)
 except subprocess.TimeoutExpired:
  p.kill();p.communicate();return None
 if p.returncode!=0:return None
 return out
# The privileged socket client has no default schema; locate the single storefront database.
schemas=sqlrun("SELECT TABLE_SCHEMA FROM information_schema.TABLES WHERE TABLE_NAME='shop_product' AND TABLE_SCHEMA NOT IN ('mysql','sys','information_schema','performance_schema');",timeout=12)
names=sorted(set(x.strip() for x in (schemas or '').splitlines() if re.match(r'^[A-Za-z0-9_]+
 result=catalog_query('EXPLAIN '+query,timeout=24)
 if result is None:
  emit('EXPLAIN',{'rank':rank,'slow_seconds':seconds,'fingerprint':fingerprint,'available':False})
  continue
 rows=[]
 for line in result.splitlines():
  values=line.split('\t')
  if len(values)<10:continue
  def clean(s):return s.replace('\\n',' ').replace('\n',' ')[:150]
  rows.append({'table':clean(values[2]),'access':clean(values[3]),
   'candidate_indexes':clean(values[4]),'chosen_index':clean(values[5]),
   'estimated_rows':clean(values[8]),'extra':clean(values[9])})
 emit('EXPLAIN',{'rank':rank,'slow_seconds':seconds,'fingerprint':fingerprint,'available':True,'tables':rows[:12]})
# Table index statistics read-only for potential bottlenecks.
for table in ('shop_product','shop_product_skus','shop_product_features'):
 x=catalog_query('SHOW INDEX FROM '+table+';',timeout=14)
 if x is None:
  emit('INDEXES',{'table':table,'available':False});continue
 indexes=collections.defaultdict(list)
 for line in x.splitlines():
  col=line.split('\t')
  if len(col)<7:continue
  try:order=int(col[3])
  except:continue
  indexes[col[2]].append((order,col[4]))
 emit('INDEXES',{'table':table,'indexes':{k:[name for _,name in sorted(v)] for k,v in indexes.items()}})
PY
'''
if __name__=='__main__':
 response=execute(REMOTE)
 for line in response.splitlines():
  if line.startswith('PERF69_'):print(line,flush=True)
 if 'PERF69_EXPLAIN=' not in response:
  raise RuntimeError('No execution plans collected')
,x.strip())))
if len(names)!=1:
 raise RuntimeError('Expected exactly one database with shop_product table, got '+str(len(names)))
schema=names[0]
def catalog_query(query,timeout=20):
 return sqlrun('USE '+schema+';\n'+query,timeout=timeout)
emit('SCHEMA',{'selected':True,'matching_schemas':len(names)})
for rank,(seconds,fingerprint,query) in enumerate(selected,1):
 result=sqlrun('EXPLAIN '+query,timeout=24)
 if result is None:
  emit('EXPLAIN',{'rank':rank,'slow_seconds':seconds,'fingerprint':fingerprint,'available':False})
  continue
 rows=[]
 for line in result.splitlines():
  values=line.split('\t')
  if len(values)<10:continue
  def clean(s):return s.replace('\\n',' ').replace('\n',' ')[:150]
  rows.append({'table':clean(values[2]),'access':clean(values[3]),
   'candidate_indexes':clean(values[4]),'chosen_index':clean(values[5]),
   'estimated_rows':clean(values[8]),'extra':clean(values[9])})
 emit('EXPLAIN',{'rank':rank,'slow_seconds':seconds,'fingerprint':fingerprint,'available':True,'tables':rows[:12]})
# Table index statistics read-only for potential bottlenecks.
for table in ('shop_product','shop_product_skus','shop_product_features'):
 x=sqlrun('SHOW INDEX FROM '+table+';',timeout=14)
 if x is None:
  emit('INDEXES',{'table':table,'available':False});continue
 indexes=collections.defaultdict(list)
 for line in x.splitlines():
  col=line.split('\t')
  if len(col)<7:continue
  try:order=int(col[3])
  except:continue
  indexes[col[2]].append((order,col[4]))
 emit('INDEXES',{'table':table,'indexes':{k:[name for _,name in sorted(v)] for k,v in indexes.items()}})
PY
'''
if __name__=='__main__':
 response=execute(REMOTE)
 for line in response.splitlines():
  if line.startswith('PERF69_'):print(line,flush=True)
 if 'PERF69_EXPLAIN=' not in response:
  raise RuntimeError('No execution plans collected')
