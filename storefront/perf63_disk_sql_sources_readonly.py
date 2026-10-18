#!/usr/bin/env python3
"""Read-only second-stage profiling: sanitized SQL fingerprints, time correlation, storage breakdown."""
from stage52_disable_callback import execute
REMOTE=r'''set -eu
python3 - <<'PY'
import os,re,json,pathlib,subprocess,collections,datetime
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
def emit(key,d):print('PERF63_'+key+'='+json.dumps(d,ensure_ascii=False),flush=True)
def run(a,t=20):
 try:
  p=subprocess.Popen(a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  o,e=p.communicate(timeout=t)
  return o if p.returncode==0 else ''
 except Exception:return ''
for directory in ['wa-data','wa-data/public','wa-data/private','wa-data/protected','wa-data/public/shop','wa-data/public/site','wa-data/public/shop/products','wa-data/public/shop/themes','wa-cache']:
 path=root/directory
 if not path.is_dir():continue
 o=run(['du','-k','--max-depth=1',str(path)],t=50)
 pairs=[]
 for l in o.splitlines():
  z=l.split('\t',1)
  if len(z)==2:
   try:gb=round(int(z[0])/1024/1024,2)
   except:continue
   if z[1]!=str(path):
    pairs.append({'dir':pathlib.Path(z[1]).name,'gb':gb})
 emit('STORAGE',{'path':directory,'total_gb':next((round(int(x.split('\t',1)[0])/1024/1024,2) for x in o.splitlines() if x.endswith('\t'+str(path))),None),
  'top':sorted(pairs,key=lambda x:x['gb'],reverse=True)[:17]})
log=pathlib.Path('/var/log/mysql/mariadb-slow.log')
raw=log.read_text('utf-8',errors='replace')[-280000:] if log.is_file() else ''
blocks=re.split(r'(?m)^# Time:',raw)
shapes=collections.defaultdict(lambda:{'n':0,'max':0,'total':0,'first_date':None,'last_date':None,'example':None})
for blk in blocks:
 m=re.search('Query_time:\\s*([0-9.]+)',blk)
 if not m:continue
 dur=float(m.group(1))
 date=re.search(r'\\A\\s*([0-9]{6}\\s+\\d\\d:\\d\\d:\\d\\d|[0-9]{4}-[0-9]{2}-[0-9]{2}T[^\\s]+)',blk)
 # Strip literal strings, token values, comments and all identifiers not SQL keywords/known table names.
 lines=blk.splitlines()
 start=next((i for i,l in enumerate(lines) if l.startswith('SET timestamp=')),None)
 sql=' '.join(lines[start+1:]) if start is not None else ''
 if not sql:continue
 sql=re.sub(r"'(?:''|\\\\'|[^'])*'","?",sql)
 sql=re.sub(r'"(?:\\\\"|[^"])*"','?',sql)
 sql=re.sub(r'\\b\\d+(?:\\.\\d+)?\\b','?',sql)
 sql=' '.join(sql.split())
 fingerprint=sql[:420]
 key=str(hash(fingerprint))
 x=shapes[key]
 x['n']+=1;x['max']=max(x['max'],dur);x['total']+=dur
 if date:
  if not x['first_date']:x['first_date']=date.group(1)
  x['last_date']=date.group(1)
 if not x['example']:
  # Only query structure: first SQL keyword, JOIN, WHERE, ORDER BY, GROUP BY with sanitized column names.
  tables=sorted(set(re.findall(r'\\b(?:FROM|JOIN)\\s+[\x60]?([a-z_0-9]+)',sql,re.I)))
  x['example']={'operation':re.search(r'\\b(SELECT|UPDATE|INSERT|DELETE)\\b',sql,re.I).group(1) if re.search(r'\\b(SELECT|UPDATE|INSERT|DELETE)\\b',sql,re.I) else 'unknown',
    'tables':tables,'sql_structure':fingerprint[:380]}
emit('SQL_SHAPES',{'group_count':len(shapes),'groups':sorted(list(shapes.values()),key=lambda x:x['total'],reverse=True)[:12]})
# Source references: names only. Read exact PHP hooks safely: no secret file contents printed.
needles=['shop_product_features','shop_product_skus','shopProductsCollection','product_features']
targets=[root/'wa-apps/shop/lib',root/'wa-apps/shop/plugins']
matches=[]
for basedir in targets:
 if not basedir.is_dir():continue
 for p in basedir.rglob('*.php'):
  if len(matches)>=95:break
  try:
   if p.stat().st_size>450000:continue
   txt=p.read_text('utf8',errors='replace')
  except:continue
  found=[n for n in needles if n in txt]
  if found:
   matches.append({'path':str(p.relative_to(root))[:190],'terms':found})
emit('CODE_MATCHES',{'count_limited':len(matches),'files':matches[:95]})
PY
'''
if __name__=='__main__':
 result=execute(REMOTE)
 for line in result.splitlines():
  if line.startswith('PERF63_'):print(line,flush=True)
 if 'PERF63_SQL_SHAPES=' not in result:
  raise RuntimeError('Second stage not complete')
