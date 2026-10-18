#!/usr/bin/env python3
"""Read-only audit of active canonical source and relevant flags."""
import json
from stage52_disable_callback import execute

REMOTE = r'''set -eu
python3 - <<'PY'
import pathlib,json,hashlib
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
path=root/'wa-data/public/site/themes/pureMegapolis42/index.html'
s=path.read_text(encoding='utf-8')
lines=s.splitlines()
hits=[]
for i,line in enumerate(lines):
 if ('CANONICAL' in line or 'canonical' in line or '_seo_pagination' in line or 'Scoped search indexing' in line) and i<260:
  hits.append({'line':i+1,'context':'\n'.join(lines[max(0,i-3):min(len(lines),i+7)])[:900]})
# Find where the response head and SEO plugin inject their canonical tags.
sources=[]
for rel in ('wa-data/public/site/themes/pureMegapolis42/index.html',
            'wa-data/public/shop/themes/pureMegapolis42/head.html'):
 q=root/rel
 if q.is_file():
  for no,line in enumerate(q.read_text(encoding='utf-8',errors='replace').splitlines(),1):
   if any(a in line.lower() for a in ('wa->head()', 'wa->head', 'canonical','shop->head')):
    sources.append({'path':rel,'line':no,'code':line.strip()[:280]})
plugin=root/'wa-apps/shop/plugins/seo'
plugin_hits=[]
if plugin.is_dir():
 for file in plugin.rglob('*.php'):
  if len(plugin_hits)>=35:break
  if file.stat().st_size>250000:continue
  try:
   for no,line in enumerate(file.read_text(encoding='utf-8',errors='replace').splitlines(),1):
    if 'canonical' in line.lower() and len(plugin_hits)<35:
     plugin_hits.append({'path':str(file.relative_to(root)),'line':no,'code':line.strip()[:180]})
  except Exception:pass
print('HEAD_ORIGIN_AUDIT='+json.dumps({'theme_references':sources[:35],'plugin_mentions':plugin_hits},ensure_ascii=False),flush=True)
print('CANONICAL_AUDIT='+json.dumps({'sha256':hashlib.sha256(s.encode()).hexdigest(),'bytes':len(s),'count_anchor':s.count('{* CANONICAL *}'),'count_simple':s.count('<link rel="canonical" href="{$canonical|escape}" />'),'count_pagination':s.count('$_seo_pagination_number'),'hits':hits[:15]},ensure_ascii=False),flush=True)
logs=root/'wa-log'
results={'files':[],'errors':[]}
if logs.is_dir():
 for p in sorted(logs.rglob('*.log')):
  if len(results['files'])>=35:break
  try:
   if not p.is_file():continue
   stat=p.stat()
   results['files'].append({'file':str(p.relative_to(logs)),'bytes':stat.st_size})
   if stat.st_size==0:continue
   with p.open('rb') as fp:
    fp.seek(max(0,stat.st_size-260000))
    part=fp.read().decode('utf-8','replace')
   for row in part.splitlines():
    if any(tag in row.lower() for tag in ('smarty', 'syntax error', 'cannot use', 'unexpected', 'pagination')):
     results['errors'].append({'file':str(p.relative_to(logs)),'message':row[:230]})
  except Exception:pass
print('CANONICAL_LOG_AUDIT='+json.dumps({'files':results['files'],'error_tail':results['errors'][-20:]},ensure_ascii=False),flush=True)
cfg=root/'wa-config/apps/shop/plugins.php'
action=root/'wa-apps/shop/lib/actions/frontend/shopFrontendCategory.action.php'
intext=path.read_text(encoding='utf-8')
head_hits=[{'line':i+1,'text':'\\n'.join(intext.splitlines()[max(0,i-2):i+3])[:450]} for i,line in enumerate(intext.splitlines()) if 'wa->head(' in line]
payload={'head_helpers':head_hits[:6],'plugin_config_exists':cfg.is_file()}
if cfg.is_file():
 code=cfg.read_text(encoding='utf-8')
 payload['plugin_config_bytes']=len(code)
 payload['plugin_config_active_names']=[m.group(1) for m in __import__('re').finditer(r"'([a-z0-9_]+)'\\s*=>",code)][:40]
if action.is_file():
 lines=action.read_text(encoding='utf-8').splitlines()
 payload['category_action_snippets']=[{'line':i+1,'text':'\\n'.join(lines[max(0,i-3):i+4])[:900]} for i,line in enumerate(lines) if any(term in line for term in ('frontend_category','setCanonical','canonical'))][:14]
print('CANONICAL_PLUGIN_CONTEXT='+json.dumps(payload,ensure_ascii=False),flush=True)
# Read only current plugin registry structure, not settings or credentials.
import re
registry=[]
if cfg.is_file():
 for i,line in enumerate(cfg.read_text(encoding='utf-8').splitlines()):
  if '=>' in line or 'return' in line or 'array(' in line or line.strip() in ('<?php',');'):
   safe=re.sub(r'(token|password|secret|api_key)[^=]*=>.*', '[REDACTED]',line,flags=re.I)
   registry.append(str(i+1)+': '+safe[:160])
samples=[]
plugins=root/'wa-apps/shop/plugins'
for name in ('megasuppliers','ozonstocksync','seo'):
 d=plugins/name
 for rel in ('lib/config/plugin.php','lib/shop'+name.capitalize()+'Plugin.class.php'):
  p=d/rel
  if p.is_file():
   chunk=p.read_text(encoding='utf-8')[:850]
   samples.append({'plugin':name,'file':rel,'example':chunk if rel.endswith('plugin.php') and not re.search('token|password|api_key|secret',chunk,re.I) else '[OMITTED]'})
print('CANONICAL_PLUGIN_REGISTRY='+json.dumps({'lines':registry[:65],'samples':samples},ensure_ascii=False),flush=True)
PY
'''
if __name__ == '__main__':
    result=execute(REMOTE)
    rows=[line for line in result.splitlines() if line.startswith('CANONICAL_AUDIT=')]
    if len(rows)!=1:
        raise RuntimeError('Missing audit output')
    print(rows[0],flush=True)
    for line in result.splitlines():
        if line.startswith(('CANONICAL_LOG_AUDIT=','CANONICAL_PLUGIN_CONTEXT=','CANONICAL_PLUGIN_REGISTRY=')):
            print(line,flush=True)
