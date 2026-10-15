#!/usr/bin/env python3
"""Read-only capability audit for installed licensed YML PRO.

Reports class names, filenames, hook identifiers and feature flags only.
Never uploads or prints proprietary code, credentials or product data.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "storefront"))
from stage52_disable_callback import execute

REMOTE = r'''set -euo pipefail
python3 - <<'PY'
import pathlib,re,json,collections
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
base=root/'wa-apps/shop/plugins/yml'
assert base.is_dir()
paths=[f for f in base.rglob('*.php') if f.is_file()]
def content(f):
 try: return f.read_text(encoding='utf-8',errors='replace')
 except Exception: return ''
def short(f):return str(f.relative_to(base))
def boolcode(s,regex):return bool(re.search(regex,s,re.I))
config=base/'lib/config/plugin.php'
entry=base/'lib/shopYml.plugin.php'
details={}
s=content(config)
details['event_keys'] = list(dict.fromkeys(re.findall(r"""['"]([a-z_]+(?:\.[a-z_]+)?)['"]\s*=>""", s)))[:65]
details['plugin_class_methods'] = re.findall(r'function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(',content(entry))[:60]
details['plugin_declared_hooks'] = re.findall(r"""['"]([a-z_]+(?:\.[a-z_]+)?)['"]\s*=>\s*['"]([a-zA-Z0-9_]+)['"]""",s)[-60:]
def candidate(f):
 p=short(f).lower()
 return any(x in p for x in ('image','picture','photo','media','download','import/handler','import/processor','import/pipeline','import/batch'))
matches=[]
for f in paths:
 if not candidate(f):continue
 t=content(f)
 classes=re.findall(r'\b(?:class|trait|interface)\s+([A-Za-z_][A-Za-z_0-9]*)',t)
 methods=re.findall(r'\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(',t)
 flags={
 'photo_refs':len(re.findall(r'\b(?:picture|images|image|photo|thumb)s?\b',t,re.I)),
 'product_image_save':boolcode(t,r'shopProductImagesModel|shopProductImages|->addImage\s*\(|->addImages\s*\('),
 'file_download':boolcode(t,r'curl_exec|file_get_contents|download|->getContent\s*\(|->getFile\s*\('),
 'summary_write':boolcode(t,r'(?:summary|setSummary|\[extimg\])'),
 'event_dispatch':boolcode(t,r'wa\(\)->event\s*\(|wa\(\)->getEvent|->event\s*\('),
 'php_wrapped':boolcode(t,r'ioncube|sourceguardian|zend_loader')
 }
 if flags['photo_refs'] or flags['product_image_save']:
  matches.append({'file':short(f),'classes':classes[:4],'methods':[m for m in methods if re.search(r'image|photo|picture|download|save|upload|process|prepare|import|filter|handle|create|finish|start|init|before|after',m,re.I)][:22],'flags':flags})
matches=sorted(matches,key=lambda m:(not (m['flags']['product_image_save'] and m['flags']['photo_refs']),not m['flags']['file_download'],m['file']))
print('YMLCAP_MAIN='+json.dumps(details,ensure_ascii=False))
print('YMLCAP_CANDIDATES='+json.dumps(matches[:80],ensure_ascii=False))
events=[]
hot=[]
for f in paths:
 t=content(f)
 if not t:continue
 # grep event wiring, but publish only method identifiers and filename.
 if re.search(r'wa\(\)->event\s*\(|wa\(\)->getEvent\s*\(|->callEvent\s*\(',t):
  hot.append({'file':short(f),'methods':re.findall(r'\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(',t)[:20]})
 if 'backend_product' in t or 'product_save' in t:
  if len(events)<35:events.append(short(f))
print('YMLCAP_EVENT_DISPATCH='+json.dumps(hot[:25],ensure_ascii=False))
print('YMLCAP_EVENT_REFERENCES='+json.dumps(events[:35],ensure_ascii=False))
# Licensing markers: audit only existence and method names, not content.
license_names=[short(f) for f in base.rglob('*') if f.is_file() and re.search(r'licen[cs]e|copyright',f.name,re.I)]
print('YMLCAP_LICENSE_FILES='+json.dumps(license_names[:12],ensure_ascii=False))
PY'''
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith("YMLCAP_"):print(line,flush=True)
if 'YMLCAP_CANDIDATES=' not in out: raise RuntimeError('Incomplete read-only capability audit')
