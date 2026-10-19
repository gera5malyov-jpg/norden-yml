#!/usr/bin/env python3
"""Reduce mobile product gallery footprint without cropping images.
This change modifies only one scoped theme style. No product/source images or SKUs.
"""
from stage52_disable_callback import execute

REMOTE=r'''set -eu
python3 - <<'PY'
import os,re,json,pathlib,shutil,hashlib,datetime,tempfile,subprocess
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
file=root/'wa-data/public/site/themes/pureMegapolis42/index.html'
marker='megapolis-gallery-cls-20261019'
def say(key,obj):
 print('GALLERY90_'+key+'='+json.dumps(obj,ensure_ascii=False),flush=True)
def fetch(path):
 p=subprocess.Popen(['curl','-ksSL','--compressed','--max-time','28','-w','\n%{http_code}',
   'https://profikompany.ru'+path],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 out,err=p.communicate(timeout=37)
 body,sep,status=out.rpartition('\n')
 head=body.split('</head>',1)[0]
 return {
  'status':status,'rc':p.returncode,'body':body,
  'title':re.findall(r'<title[^>]*>(.*?)</title>',head,re.I|re.S),
  'robots':re.findall(r'<meta[^>]+name=["\']robots["\'][^>]*>',head,re.I),
  'canonical':re.findall(r'<link[^>]+rel=["\']canonical["\'][^>]*>',head,re.I),
  'productgallery':body.count('product-gallery--extimg'),'thumbnails':body.count('class="product-gallery__thumb js-extimg-thumb'),
  'form':'id="s-product-form"' in body,
  'purchase':'megapolis-orderable-20261019' in body,
  'trust':'megapolis-verified-trust-20261019' in body,
  'menu':'class="megapolis-menu-quick"' in body,
  'font76':'montserrat.megapolis76.css' in body,
 }
before=file.read_bytes();text=before.decode('utf8')
pattern=r'(<style id="megapolis-gallery-cls-20261019">)(.*?)(</style>)'
matches=list(re.finditer(pattern,text,re.S))
if len(matches)!=1 or text.count(marker)!=1:
 raise RuntimeError('Preflight 1: unique current gallery marker absent')
old_style=matches[0].group(2)
if ('aspect-ratio:1/1' not in old_style or 'max-height:100%;object-fit:contain' not in old_style
    or '.product-gallery--extimg .product-gallery__main' not in old_style):
 raise RuntimeError('Preflight 1: previous gallery CSS changed, refusing to overwrite')
say('PRE',{'check':1,'ok':True,'sha256':hashlib.sha256(before).hexdigest(),'old_style_length':len(old_style)})
routes=[
 '/shkafa-vysokiy-s-glukhimi-dvermi-b-4306-burgundi-900kh450kh2054-born/',
 '/shkafa-vysokiy-s-glukhimi-dvermi-b-4306-venge-magiya-900kh450kh2054-born/',
 '/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/',
 '/stul-prozrachnyy-pedrali-queen/',
 '/',
 '/category/shkafy/',
 '/category/kompyuternye-kresla/',
 '/compare/1186404,1186400/',
 '/search/?query=stul',
 '/cart/',
 '/oplata/',
 '/garantiya/'
]
baseline=[fetch(url) for url in routes]
if not all(x['status']=='200' and x['rc']==0 and x['title'] for x in baseline):
 raise RuntimeError('Preflight 2: site pages not healthy')
if baseline[0]['productgallery']<1 or baseline[0]['thumbnails']<3 or not baseline[0]['form']:
 raise RuntimeError('Preflight 2: screenshot product gallery or controls missing')
if not baseline[2]['purchase'] or not baseline[2]['trust'] or not baseline[0]['menu']:
 raise RuntimeError('Preflight 2: previous product and mobile navigation changes absent')
say('PRE',{'check':2,'ok':True,'pages':len(routes),'sample_product_thumbs':baseline[0]['thumbnails']})
if not all('noindex' in ''.join(baseline[i]['robots']).lower() for i in (7,8)):
 raise RuntimeError('Preflight 3: compare/search noindex missing')
if any('noindex' in ''.join(baseline[i]['robots']).lower() for i in (0,2,5,6)):
 raise RuntimeError('Preflight 3: noindex leaked into products/categories')
say('PRE',{'check':3,'ok':True,'search_and_comparison_seo_retained':True})
css="""\n    /* megapolis-gallery-mobile-fit-v2: full photo without cropping, compact mobile preview. */\n    @media(max-width:767.98px){\n      .product-gallery--extimg .product-gallery__main{\n        height:clamp(220px,36vh,320px);\n        aspect-ratio:auto;\n        display:flex;\n        align-items:center;\n        justify-content:center;\n        overflow:hidden;\n      }\n      .product-gallery--extimg .product-gallery__main img{\n        display:block;\n        width:100%;\n        height:100%;\n        max-width:100%;\n        max-height:100%;\n        object-fit:contain;\n        object-position:center;\n      }\n    }\n    """
if '.product-gallery--extimg' not in css or 'object-fit:contain' not in css:
 raise RuntimeError('New responsive style invalid')
updated=text[:matches[0].start(2)]+css+text[matches[0].end(2):]
if updated.count(marker)!=1 or updated.count('megapolis-gallery-mobile-fit-v2')!=1:
 raise RuntimeError('Generated patch failed')
folder=root.parent/'storefront-backups'/('gallery90-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700,parents=True)
backup=folder/'index.html';shutil.copy2(str(file),str(backup));os.chmod(str(backup),0o600)
if backup.read_bytes()!=before:raise RuntimeError('Backup verification failed')
say('BACKUP',{'path':str(backup),'sha256':hashlib.sha256(before).hexdigest()})
changed=False
try:
 stat=file.stat()
 with tempfile.NamedTemporaryFile('wb',dir=str(file.parent),prefix='.gallery90-',delete=False) as fp:
  fp.write(updated.encode('utf8'));tmp=fp.name
 os.chmod(tmp,stat.st_mode & 0o777);os.chown(tmp,stat.st_uid,stat.st_gid)
 if file.read_bytes()!=before:raise RuntimeError('Concurrent theme change detected')
 os.replace(tmp,str(file));changed=True
 for i,path in enumerate(routes):
  after=fetch(path);orig=baseline[i]
  stable=(after['rc']==0 and after['status']=='200' and after['title']==orig['title']
   and after['canonical']==orig['canonical'] and after['robots']==orig['robots']
   and after['productgallery']==orig['productgallery'] and after['thumbnails']==orig['thumbnails']
   and after['form']==orig['form'] and after['purchase']==orig['purchase']
   and after['trust']==orig['trust'] and after['menu']==orig['menu']
   and after['font76']==orig['font76'])
  has_gallery_style='megapolis-gallery-cls-20261019' in orig['body']
  css_ok=(('megapolis-gallery-mobile-fit-v2' in after['body'] and 'height:clamp(220px,36vh,320px)' in after['body']) if has_gallery_style else 'megapolis-gallery-mobile-fit-v2' not in after['body'])
  ok=bool(stable and css_ok)
  say('TEST',{'number':i+1,'path':path,'ok':ok,'http':after['status'],'gallery_links_kept':after['thumbnails']==orig['thumbnails']})
  if not ok:
   fields=['rc','status','title','robots','canonical','productgallery','thumbnails','form','purchase','trust','menu','font76']
   say('DIFF',{'route':path,'changed':{key:{'before':str(orig[key])[:130],'after':str(after[key])[:130]} for key in fields if orig[key]!=after[key]},'css_ok':css_ok})
   raise RuntimeError('Storefront postcheck failed: '+path)
 if file.read_text('utf8')!=updated:raise RuntimeError('Live file changed unexpectedly')
 say('DONE',{'success':True,'postchecks':len(routes),'backup':str(backup),
  'fit':'contain','mobile_gallery_max_height_px':320,'source_images_unchanged':True,
  'catalog_prices_stocks_orders_untouched':True})
except Exception as ex:
 if changed and file.read_text('utf8')==updated:
  shutil.copy2(str(backup),str(file))
  say('ROLLBACK',{'restored':True,'error':str(ex)[:220]})
 raise
PY
'''
if __name__=='__main__':
 out=execute(REMOTE)
 for line in out.splitlines():
  if line.startswith('GALLERY90_'):print(line,flush=True)
 if 'GALLERY90_DONE=' not in out:raise RuntimeError('Mobile gallery change not verified')
