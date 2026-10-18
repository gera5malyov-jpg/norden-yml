#!/usr/bin/env python3
"""Read-only selectivity checks for the slow Webasyst feature/SKU filters."""
from stage52_disable_callback import execute
REMOTE=r'''set -eu
python3 - <<'PY'
import subprocess,re,pathlib,json,collections
def emit(k,v):print('PERF71_'+k+'='+json.dumps(v,ensure_ascii=False),flush=True)
php="$c=include '/home/web/vm-23f9aff9.na4u.ru/www/wa-config/db.php';echo $c['default']['database'];"
p=subprocess.Popen(['/usr/bin/php7.4','-r',php],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
schema,err=p.communicate(timeout=10)
schema=schema.strip()
if p.returncode!=0 or re.fullmatch('[A-Za-z0-9_]+',schema) is None:raise RuntimeError('Cannot select storefront DB')
def sql(s,limit=35):
 p=subprocess.Popen(['mysql','--protocol=socket','-uroot','-N','-B'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 try:out,err=p.communicate(input='USE '+schema+';\n'+s,timeout=limit)
 except subprocess.TimeoutExpired:p.kill();p.communicate();return None
 return out.strip() if p.returncode==0 else None
raw=pathlib.Path('/var/log/mysql/mariadb-slow.log').read_text('utf8',errors='replace')[-450000:]
pairs=collections.Counter()
for block in re.split(r'(?m)^# Time:',raw):
 lines=block.splitlines()
 i=next((i for i,l in enumerate(lines) if l.startswith('SET timestamp=')),None)
 if i is None:continue
 q=' '.join(lines[i+1:])
 if not q.lstrip().upper().startswith('SELECT') or 'shop_product_features' not in q:continue
 a=re.search(r'pf1\.feature_id\s*=\s*(\d+)',q)
 b=re.search(r'pf1\.feature_value_id\s+IN\s*\(\s*(\d+)\s*\)',q,re.I)
 if a and b:pairs[(a.group(1),b.group(1))]+=1
emit('PRECHECK',{'matched_pairs':len(pairs),'frequent':min(4,len(pairs))})
if not pairs:raise RuntimeError('No exact feature ID/value match in slow log')
for rank,((feature,value),freq) in enumerate(pairs.most_common(4),1):
 count=sql('SELECT COUNT(*) FROM shop_product_features WHERE feature_id='+feature+' AND feature_value_id='+value+';',36)
 emit('SELECTIVITY',{'rank':rank,'log_occurrences':freq,'row_count':int(count) if count and count.isdigit() else None,
   'feature_matches_filtered_rows':count is not None})
PY
'''
if __name__=='__main__':
 x=execute(REMOTE)
 for line in x.splitlines():
  if line.startswith('PERF71_'):print(line,flush=True)
 if 'PERF71_SELECTIVITY=' not in x:raise RuntimeError('No selectivity data')
