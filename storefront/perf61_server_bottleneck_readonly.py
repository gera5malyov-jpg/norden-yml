#!/usr/bin/env python3
"""Read-only bottleneck inventory for profikompany.ru. No content, DB or cache mutations."""
from stage52_disable_callback import execute

REMOTE = r'''set -eu
python3 - <<'PY'
import os,sys,pathlib,re,json,subprocess,collections,time,glob
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
def emit(tag,data):
 print('PERF61_'+tag+'='+json.dumps(data,ensure_ascii=False),flush=True)
def run(args,timeout=7):
 try:
  p=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  stdout,stderr=p.communicate(timeout=timeout)
  return stdout if p.returncode==0 else ''
 except Exception:return ''
def safe_execs():
 for app in ('nginx','php-fpm','php-fpm8.2','php-fpm8.3','php','mysql','mariadb','redis-server','memcached','docker'):
  found=run(['which',app]).strip()
  if found:emit('BINARY',{'name':app,'path':found[:100]})
safe_execs()
emit('SYSTEM',{'python':sys.version.split()[0],'uptime':run(['uptime'])[:200],
  'memory':run(['free','-m'])[:800],'disk':run(['df','-h',str(root)])[:700]})
process=run(['ps','-eo','comm'],timeout=9).splitlines()
tally=collections.Counter(x.strip().split('/')[-1] for x in process)
emit('PROCESSES',{'counts':[(k,v) for k,v in tally.most_common(35)]})
docker=run(['docker','ps','--format','{{.Names}}|{{.Image}}'],timeout=9).splitlines()
emit('CONTAINERS',{'count':len(docker),'names_images':[line[:190] for line in docker[:22]]})
for key in ['wa-config','wa-cache','wa-apps/shop','wa-apps/site','wa-data/public/site/themes/pureMegapolis42','wa-data/public/shop/themes/pureMegapolis42']:
 path=root/key
 emit('PATH',{'name':key,'exists':path.exists(),'symlink':path.is_symlink(),'resolved':str(path.resolve())[:170] if path.exists() else ''})
# Inspect only file metadata and known safe performance flags, never print configuration values.
for key in ['wa-config/config.php','wa-config/db.php','wa-config/cache.php','wa-config/apps.php','.htaccess']:
 path=root/key
 if path.is_file():
  text=path.read_text('utf8',errors='replace')
  emit('CONFIG',{'name':key,'bytes':len(text),'cache_words':len(re.findall('cache',text,re.I)),
    'host_db':bool(re.search(r'mysql|mysqli|mariadb|postgres',text,re.I)),
    'debug':bool(re.search(r'debug',text,re.I))})
# Logs: only filename + size, do not print log contents or private paths.
for pattern in ['/var/log/nginx/*','/var/log/php*/*','/var/log/mysql/*','/var/log/mariadb/*']:
 files=[pathlib.Path(x) for x in glob.glob(pattern) if pathlib.Path(x).is_file()]
 emit('LOGS',{'pattern':pattern,'count':len(files),
  'files':[{'file':p.name,'kb':round(p.stat().st_size/1024)} for p in files[:22]]})
# Detect huge cache item count without crawling millions of files.
for key in ['wa-cache','wa-cache/apps/shop','wa-cache/cache','wa-log']:
 path=root/key
 if path.is_dir():
  try:
   entries=list(path.iterdir())
   emit('CACHE',{'path':key,'direct_entries':len(entries),'top_names':[p.name[:70] for p in entries[:24]]})
  except Exception as e:emit('CACHE',{'path':key,'error':type(e).__name__})
# Theme templates that directly request costly catalog collections.
for key in ['wa-data/public/site/themes/pureMegapolis42/index.html','wa-data/public/site/themes/pureMegapolis42/layouts/layout.slider.html','wa-data/public/shop/themes/pureMegapolis42/home.html','wa-data/public/shop/themes/pureMegapolis42/category.html','wa-data/public/shop/themes/pureMegapolis42/product.html']:
 path=root/key
 if path.is_file():
  source=path.read_text('utf8',errors='replace')
  hits=[]
  for i,line in enumerate(source.splitlines()):
   if any(s in line for s in ['$wa->shop->category(','$wa->shop->products(','$wa->shop->product(','$wa->shop->categories(','{shop','{include','{foreach','{if $wa->']):
    hits.append({'line':i+1,'snippet':line.strip()[:250]})
  emit('TEMPLATE',{'file':key,'bytes':len(source),'hits':hits[:55]})
# Plugin hook counts, no files executed, read only manifest/sources.
plugins=root/'wa-apps/shop/plugins'
if plugins.is_dir():
 rows=[]
 for folder in plugins.iterdir():
  if not folder.is_dir():continue
  for name in ('lib/config/plugin.php','lib/config/hooks.php'):
   f=folder/name
   if f.is_file():
    x=f.read_text('utf8',errors='replace')
    if 'hooks' in x or 'frontend' in x:
     rows.append({'plugin':folder.name,'manifest':name,'hooks':len(re.findall('frontend|hook|routing',x,re.I)),'kb':round(len(x)/1024,1)})
 emit('HOOK_FILES',{'count':len(rows),'sample':rows[:95]})
# Test localhost with Host header, low volume and zero cache flush; measure time to headers.
for target in ('/','/category/kompyuternye-kresla/','/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','/oplata/'):
 p=run(['curl','-kLsS','--max-time','16','-o','/dev/null','-w','%{http_code}|%{time_starttransfer}|%{time_total}',
   'https://profikompany.ru'+target],timeout=21)
 emit('HTTP',{'route':target,'http_ttfb_total':p.strip()[:80]})
PY
'''

if __name__ == '__main__':
    output=execute(REMOTE)
    for line in output.splitlines():
        if line.startswith('PERF61_'):print(line,flush=True)
    if 'PERF61_HTTP=' not in output:
        raise RuntimeError('Server inventory incomplete')
