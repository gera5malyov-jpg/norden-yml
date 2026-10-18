#!/usr/bin/env python3
"""Read-only inventory of Webasyst performance configuration and theme collection costs."""
from stage52_disable_callback import execute

REMOTE=r'''set -eu
python3 - <<'PY'
import json,os,pathlib,re,subprocess
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
def info(name, data):
 print('STAGE59_SERVER='+json.dumps({'check':name,'data':data},ensure_ascii=False),flush=True)
def output(argv,seconds=6):
 try:
  p=subprocess.run(argv,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=seconds)
  return p.stdout.strip()[:1800] if p.returncode==0 else ''
 except Exception:return ''
info('environment',{'uptime':output(['uptime']),
   'free':output(['free','-m']),
   'php_version':output(['php','-r','echo PHP_VERSION;']),
   'available_services':{x:bool(output(['which',x])) for x in ['redis-cli','memcached','mysql','php-fpm']}})
for name in ['wa-config/config.php','wa-config/SystemConfig.class.php','wa-config/cache.php']:
 p=root/name
 if not p.is_file():
  info('configuration',{'name':name,'present':False})
  continue
 txt=p.read_text('utf8',errors='replace')
 # Never reveal configuration lines, passwords, API keys or connection strings.
 info('configuration',{'name':name,'present':True,'bytes':p.stat().st_size,
  'has_cache_setting':bool(re.search(r'["\\']cache["\\']\s*=>|\$this->cache\s*=',txt)),
  'cache_keywords':{k:len(re.findall(r'\b'+k+r'\b',txt,re.I)) for k in ['file','memcache','redis','apcu','debug','compile_check','developer']}})
for name in ['wa-cache','wa-data/cache','wa-log','wa-apps/shop/lib/classes/shopProductsCollection.class.php']:
 p=root/name
 info('path',{'name':name,'exists':p.exists(),'directory':p.is_dir(),'size_kb':round(p.stat().st_size/1024,1) if p.is_file() else None})
site=root/'wa-data/public/site/themes/pureMegapolis42'
shop=root/'wa-data/public/shop/themes/pureMegapolis42'
for p in [site/'index.html',shop/'category.html',shop/'product.html',site/'layouts/layout.header.html',site/'layouts/layout.categories.html',site/'layouts/layout.slider.html']:
 if not p.is_file():continue
 t=p.read_text('utf8',errors='replace')
 counts={k:t.count(k) for k in ['$wa->shop->products(', '$wa->shop->categories(', '$wa->shop->features(', '$wa->shop->category(', 'wa->shop->', 'filter', 'cache', 'Smarty', 'products(', 'include']}
 lines=t.splitlines()
 slow=[{'line':i+1,'text':line.strip()[:260]} for i,line in enumerate(lines) if re.search(r'\$wa->shop->(?:products|categories|features|category)\(|products\(|\$products',line)][:25]
 info('theme',{'file':str(p.relative_to(root)),'bytes':p.stat().st_size,'counts':counts,'collection_related':slow})
# Status flags only: never print SQL text or database credentials.
for argv in [
 ['mysql','-N','-e',"SHOW GLOBAL STATUS WHERE Variable_name IN ('Slow_queries','Threads_running','Questions','Created_tmp_disk_tables');"],
 ['mysql','-N','-e',"SHOW GLOBAL VARIABLES WHERE Variable_name IN ('slow_query_log','long_query_time','max_connections');"]
]:
 val=output(argv)
 if val:
  info('db_status',{'measurements':val.splitlines()})
 else:info('db_status',{'available':False})
# List load bearing extensions by basename only, no secrets.
plugins=root/'wa-apps/shop/plugins'
if plugins.is_dir():
 names=[p.name for p in plugins.iterdir() if p.is_dir()]
 info('shop_plugins_installed',{'count':len(names),'names':sorted(names)[:90]})
PY
'''

if __name__=='__main__':
 result=execute(REMOTE)
 for line in result.splitlines():
  if line.startswith('STAGE59_SERVER='):print(line,flush=True)
