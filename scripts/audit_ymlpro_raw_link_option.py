#!/usr/bin/env python3
"""Read-only determine meaning of native raw_image_links and image modes."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r'''set -euo pipefail
python3 - <<'PY'
import pathlib,re,json,collections
base=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www/wa-apps/shop/plugins/yml')
keywords=('raw_image_links','shouldImportImages','image_save','queueImages')
for term in keywords:
 hits=[]
 for f in base.rglob('*'):
  if not f.is_file() or f.suffix.lower() not in ('.php','.js','.html','.json','.tpl'):continue
  try:s=f.read_text(encoding='utf-8',errors='replace')
  except Exception:continue
  ms=list(re.finditer(re.escape(term),s,re.I))
  if not ms:continue
  for m in ms[:6]:
   around=s[max(0,m.start()-250):m.end()+250]
   tokens=re.findall(r'(?:->|::)([A-Za-z_][A-Za-z0-9_]*)\s*\(|\b(?:function|if|foreach|while|return|switch|case|isset|empty)\b',around)
   near=sorted(set(x for x in re.findall(r'(?:summary|picture|images|image_save|image_upload|download|queueImages|insertImage|description|raw_image_links|setImage|product_id|profile_id|link_separator|file_get_contents|shopProductImagesModel)',around,re.I)),key=str.lower)
   strgs=[]
   hits.append({'file':str(f.relative_to(base)),'line':s.count('\n',0,m.start())+1,'methods':list(dict.fromkeys(x for x in re.findall(r'\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)',s[max(0,m.start()-1800):m.start()])))[-2:],'related':near[:17],'translations':strgs[:2]})
 print('YMLKEY_'+term.upper()+'='+json.dumps(hits[:25],ensure_ascii=False))
# Only expose the public setting's short label/description, and abstract code operation locations.
s=(base/'lib/config/settings.json').read_text(encoding='utf-8',errors='replace')
try:
 data=json.loads(s)
 hits=[]
 def visit(x,where):
  if isinstance(x,dict):
   for k,v in x.items():
    if k=='raw_image_links':
     row={'path':where+'/'+k}
     if isinstance(v,dict):
      for key in ('name','title','description','label','type','control_type','value','default'):
       if key in v and isinstance(v[key],(str,int,bool)):
        row[key]=str(v[key])[:175]
     hits.append(row)
    visit(v,where+'/'+str(k))
  elif isinstance(x,list):
   for index,v in enumerate(x):
    if index<100:visit(v,where+'/'+str(index))
 visit(data,'')
 print('YML_OPTION_META='+json.dumps(hits[:15],ensure_ascii=False))
except Exception:pass
for name in ('lib/classes/import/shopYmlImages.class.php','templates/actions/profile/tabs/product-tabs/images.html'):
 s=(base/name).read_text(encoding='utf-8',errors='replace')
 d=[]
 for m in list(re.finditer('raw_image_links',s))[:8]:
  before=s[max(0,m.start()-230):m.start()]
  after=s[m.end():m.end()+500]
  words=['download','remote','url','picture','file','summary','description','image','setImage','insertImage','is_file','unlink','copy','rename','save','if','return','options','checkbox','checkbox']
  d.append({'line':s.count('\\n',0,m.start())+1,
            'before_tokens':[x for x in words if re.search(r'\\b'+re.escape(x)+r'\\b',before,re.I)],
            'after_tokens':[x for x in words if re.search(r'\\b'+re.escape(x)+r'\\b',after,re.I)],
            'after_calls':list(dict.fromkeys([]))[:12],
            'has_extimg':'[extimg]' in (before+after)})
 print('YML_OPTION_BEHAVIOR_'+name.rsplit('/',1)[-1]+'='+json.dumps(d,ensure_ascii=False))

PY'''
r=execute(REMOTE)
for l in r.splitlines():
 if l.startswith('YMLKEY_') or l.startswith('YML_OPTION_'):print(l,flush=True)
if 'YMLKEY_RAW_IMAGE_LINKS=' not in r:raise RuntimeError('No image option info')
