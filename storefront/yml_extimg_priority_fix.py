#!/usr/bin/env python3
"""Prioritize [extimg] gallery while retaining native images as fallback.

Touches only the active storefront's product.html. Never edits YML PRO,
products, image files, orders, stocks or supplier settings.
"""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from stage52_disable_callback import execute

REMOTE=r"""set -euo pipefail
python3 - <<'PY'
import hashlib,os,pathlib,shutil,subprocess,tempfile,urllib.parse,json,re
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
f=root/'wa-data/public/shop/themes/pureMegapolis42/product.html'
s=f.read_text(encoding='utf-8')
pattern=r'(\\{\\* IMAGE GALLERY \\*\\}\\s*)\\{if \\$product\\.images\\}'
replacement=r'\\1{if $product.images && ($ext_images|@count == 0)}'
updated,n=re.subn(pattern,replacement,s,count=2)
if n!=1 or 'js-extimg-main-img' not in s or 'product-gallery--extimg' not in s:
 raise RuntimeError('Gallery priority source unexpected; refusing write')
before=hashlib.sha256(s.encode()).hexdigest()
directory=root.parent/'storefront-backups'/('yml-extimg-priority-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
directory.mkdir(parents=True,mode=0o700)
backup=directory/'product.html'
shutil.copy2(str(f),str(backup));os.chmod(str(backup),0o600)
stat=f.stat()
with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(f.parent),delete=False,prefix='.extgallery-') as w:
 w.write(updated);tmp=w.name
os.chown(tmp,stat.st_uid,stat.st_gid)
os.chmod(tmp,stat.st_mode & 0o777)
if hashlib.sha256(f.read_bytes()).hexdigest()!=before:
 os.unlink(tmp);raise RuntimeError('Concurrent theme change')
os.replace(tmp,str(f))
try:
 tests=[]
 cases=[
  ('/stol-detskiy-reguliruemyy-everprof-kids-table-2-belyy/',5),
  ('/kreslo-everprof-lotus-lotus-s4-tkan-chernyy-krasnyy/',5),
  ('/'+urllib.parse.quote('Кресло-Икспресс-CX1361М-Черный-Пластик-Черный')+'/',11),
  ('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/',0)
 ]
 for url,expected in cases:
  p=subprocess.run(['curl','-ksSL','--max-time','28','-w','\n%{http_code}',
    'https://profikompany.ru'+url+'?extimg_priority_check='+str(os.getpid())],
    stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=35)
  html,status=p.stdout.rsplit('\n',1) if '\n' in p.stdout else ('','')
  thumbs=html.count('data-extimg=')
  is_extimg='js-extimg-main-img' in html
  ok=p.returncode==0 and status=='200' and 'class="product-page"' in html
  if expected:ok=ok and thumbs==expected and is_extimg
  else:ok=ok and thumbs==0 and not is_extimg and 'itemprop="image"' in html
  tests.append({'route':url[:72],'http':status,'external_photos':thumbs,
    'expected':expected,'main_external':is_extimg,'ok':ok})
 if not all(v['ok'] for v in tests):
  raise RuntimeError('Theme smoke failed: '+json.dumps(tests,ensure_ascii=False))
 print('YML_THEME_PATCH='+json.dumps({'status':'success','backup':str(backup),'tests':tests},ensure_ascii=False),flush=True)
except Exception as ex:
 if hashlib.sha256(f.read_bytes()).hexdigest()==hashlib.sha256(updated.encode()).hexdigest():
  shutil.copy2(str(backup),str(f))
  print('YML_THEME_ROLLBACK=restored',flush=True)
 raise
PY
"""
r=execute(REMOTE)
for line in r.splitlines():
 if line.startswith('YML_THEME_'):print(line,flush=True)
if 'YML_THEME_PATCH=' not in r:raise RuntimeError('Gallery priority deployment not verified')
