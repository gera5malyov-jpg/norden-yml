#!/usr/bin/env python3
"""Prefer extimg product URLs in two catalog tile templates, with guarded rollback."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from stage52_disable_callback import execute

REMOTE=r"""set -euo pipefail
python3 - <<'PY'
import os,pathlib,hashlib,shutil,tempfile,subprocess,json,datetime
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
theme=root/'wa-data/public/shop/themes/pureMegapolis42'
paths=[theme/'products.html',theme/'list-thumbs.html']
old={p:p.read_text(encoding='utf-8') for p in paths}
a=old[paths[0]]
needle='{if !$_product_image_src}'
new='{if !empty($product.summary)}'
guard='{if empty($product.image_id) && $_has_extimg}'
if a.count(needle)!=2 or a.count(guard)!=1 or a.find(needle)>a.find('{$_extimg_summary'):
 raise RuntimeError('products.html extimg branch changed; no edits')
a=a.replace(needle,new,1).replace(guard,'{if $_has_extimg}',1)
b=old[paths[1]]
anchor='{$_product_image_src = $wa->shop->productImgUrl($product, "0x200")}'
if b.count(anchor)!=1:
 raise RuntimeError('list-thumbs image branch changed; no edits')
block=r'''
{* External supplier image is preferred; native Webasyst photo stays fallback *}
{$_extimg_list_source = $product.summary|default:''}
{assign var="_extimg_list_match" value=[]}
{if $_extimg_list_source && preg_match('/\\[extimg\\](.*?)\\[\\/extimg\\]/is', $_extimg_list_source, $_extimg_list_match)}
  {$_extimg_list_block = str_replace("\r\n", "\n", $_extimg_list_match[1])}
  {$_extimg_list_block = str_replace("\r", "\n", $_extimg_list_block)}
  {$_extimg_list_lines = explode("\n", $_extimg_list_block)}
  {foreach $_extimg_list_lines as $_candidate}
    {if preg_match('~^https?://~i', trim($_candidate))}
      {$_product_image_src = trim($_candidate)}
      {break}
    {/if}
  {/foreach}
{/if}
'''
b=b.replace(anchor,anchor+'\n'+block,1)
old_desc='{if $product.summary && !empty($theme_settings.product_cart__summary)}'
if b.count(old_desc)!=1:
 raise RuntimeError('list-thumbs summary block changed; no edits')
b=b.replace(old_desc,"{if $product.summary && !empty($theme_settings.product_cart__summary) && !preg_match('/\\[extimg\\]/i', $product.summary)}",1)
replacement={paths[0]:a,paths[1]:b}
backup=root.parent/'storefront-backups'/('external-product-tiles-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ'))
backup.mkdir(parents=True,mode=0o700)
for p in paths:
 shutil.copy2(str(p),str(backup/p.name))
 os.chmod(str(backup/p.name),0o600)
patched=[]
try:
 for p in paths:
  stat=p.stat()
  if hashlib.sha256(p.read_bytes()).hexdigest()!=hashlib.sha256(old[p].encode()).hexdigest():
   raise RuntimeError('Concurrent template change')
  with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=str(p.parent),delete=False,prefix='.extimg-list-') as t:
   t.write(replacement[p]);tmp=t.name
  os.chown(tmp,stat.st_uid,stat.st_gid)
  os.chmod(tmp,stat.st_mode&0o777)
  os.replace(tmp,str(p));patched.append(p)
 tests=[]
 for path in ['/','/category/kompyuternye-kresla/','/stol-detskiy-reguliruemyy-everprof-kids-table-2-belyy/']:
  c=subprocess.run(['curl','-ksSL','--max-time','35','-w','\n%{http_code}',
   'https://profikompany.ru'+path+'?external_photo_tiles_check=1'],universal_newlines=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=43)
  html,code=c.stdout.rsplit('\n',1) if '\n' in c.stdout else ('','')
  ok=c.returncode==0 and code=='200' and len(html)>10000 and 'SmartyCompilerException' not in html
  if 'stol-detskiy' in path:ok=ok and 'js-extimg-main-img' in html
  tests.append({'path':path,'http':code,'ok':ok})
 if not all(x['ok'] for x in tests):
  raise RuntimeError('Page smoke failed '+json.dumps(tests,ensure_ascii=False))
 print('TILE_PATCH='+json.dumps({'status':'deployed','tests':tests,'backup_saved':True},ensure_ascii=False))
except Exception as e:
 for p in patched:
  if hashlib.sha256(p.read_bytes()).hexdigest()==hashlib.sha256(replacement[p].encode()).hexdigest():
   shutil.copy2(str(backup/p.name),str(p))
 print('TILE_ROLLBACK='+json.dumps({'reason':str(e),'restored':len(patched)},ensure_ascii=False))
 raise
PY
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith(("TILE_PATCH=","TILE_ROLLBACK=")):print(line,flush=True)
if "TILE_PATCH=" not in out:raise RuntimeError("External tile patch not verified")
