#!/usr/bin/env python3
"""Guarded template/asset rollout, private backups and compare-and-swap rollback."""
import ast,base64,json,sys
from pathlib import Path
from stage52_disable_callback import execute

BASE=Path(__file__).parent

def server_module(code):
    """Keep source transformations usable on the host's older Python runtime."""
    tree=ast.parse(code)
    tree.body=[n for n in tree.body if not (isinstance(n,ast.ImportFrom) and n.module=='__future__')]
    for n in ast.walk(tree):
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
            n.returns=None
            for a in list(n.args.args)+list(n.args.kwonlyargs)+list(n.args.posonlyargs):a.annotation=None
            if n.args.vararg:n.args.vararg.annotation=None
            if n.args.kwarg:n.args.kwarg.annotation=None
    return ast.unparse(tree)
AUDITED={
 'wa-data/public/site/themes/pureMegapolis42/index.html':'34d7fb6d3d18feb9322dee0beaed37dc8125e20ac6d65d7ac9cea597b4e5f969',
 'wa-data/public/shop/themes/pureMegapolis42/home.html':'644fb8f808bf124a0427ba841afae291e8ea22948640ec92c4a9a17fa4c977ab',
 'wa-data/public/site/themes/pureMegapolis42/layouts/layout.slider.html':'b5878a4612331dc4079e6986094d8b854bed1ae71beb6bdd10f2af849db643e8',
}
REMOTE=r'''set -eu
python3 - <<'PY'
import base64,hashlib,json,os,pathlib,shutil,subprocess,tempfile
from datetime import datetime,timezone
payload=json.loads(base64.b64decode('PAYLOAD_B64').decode('utf-8'))
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
site='wa-data/public/site/themes/pureMegapolis42/'
shop='wa-data/public/shop/themes/pureMegapolis42/'
changes={}
with tempfile.TemporaryDirectory(prefix='megapolis-sales-patch-') as td:
 for name,code in payload['modules'].items():(pathlib.Path(td)/name).write_text(code,encoding='utf-8')
 import sys
 sys.path.insert(0,td)
 from stage53_mobile_performance import patch_index,patch_home,patch_slider,externalize_fonts,once
 from stage54_product_filters import patch_product,patch_product_cart,patch_filters
 for rel,func in [(site+'index.html',patch_index),(shop+'home.html',patch_home),(site+'layouts/layout.slider.html',patch_slider)]:
  text=(root/rel).read_text(encoding='utf-8')
  if hashlib.sha256(text.encode()).hexdigest()!=payload['audited'][rel]:raise RuntimeError('Source changed: '+rel)
  changes[rel]=func(text).encode()
 old='<script src="{$wa_real_theme_url}js/metrika_funnel_v1.js?v=20261010a" defer></script>'
 new="{if $wa->domainUrl() == 'https://profikompany.ru'}"+'<script src="{$wa_real_theme_url}js/megapolis-funnel-v2.js?v=20261012"></script>'+"{else}"+old+"{/if}"
 changes[site+'index.html']=once(changes[site+'index.html'].decode(),old,new).encode()
 for rel,func in [(shop+'product.html',patch_product),(shop+'product.cart.html',patch_product_cart),(shop+'category.filters.html',patch_filters)]:
  changes[rel]=func((root/rel).read_text(encoding='utf-8')).encode()
 original_font=(root/(site+'css/fonts/montserrat.min.css')).read_text(encoding='utf-8')
 css,fonts=externalize_fonts(original_font)
 changes[site+'css/fonts/montserrat.megapolis53.css']=css.encode()
 for name,data in fonts.items():changes[site+'css/fonts/'+name]=data
 for rel,text in payload['assets'].items():
  if (root/rel).exists():raise RuntimeError('New asset already exists: '+rel)
  changes[rel]=text.encode()
 for rel in [site+'css/fonts/montserrat.megapolis53.css']+[site+'css/fonts/'+n for n in fonts]:
  if (root/rel).exists():raise RuntimeError('Optimized font asset exists: '+rel)
 for rel in changes:
  if not rel.startswith((site,shop)) or '..' in pathlib.PurePosixPath(rel).parts:raise RuntimeError('Invalid change target')
 folder=root.parent/'storefront-backups'/('stage53-sales-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
 folder.mkdir(mode=0o700,parents=True)
 records=[]
 for i,(rel,data) in enumerate(changes.items()):
  path=root/rel;before=path.read_bytes() if path.exists() else None
  backup=str(i)+'.bak'
  if before is not None:
   shutil.copy2(str(path),str(folder/backup));os.chmod(str(folder/backup),0o600)
  records.append({'path':rel,'backup':backup if before is not None else None,'old_sha':hashlib.sha256(before).hexdigest() if before is not None else None,'new_sha':hashlib.sha256(data).hexdigest()})
 (folder/'manifest.json').write_text(json.dumps(records));os.chmod(str(folder/'manifest.json'),0o600)
 written=[]
 try:
  for row in records:
   path=root/row['path'];data=changes[row['path']]
   current=path.read_bytes() if path.exists() else None
   if (hashlib.sha256(current).hexdigest() if current is not None else None)!=row['old_sha']:raise RuntimeError('Concurrent edit before write: '+row['path'])
   stat=path.stat() if path.exists() else (root/(site+'index.html')).stat()
   with tempfile.NamedTemporaryFile('wb',dir=str(path.parent),prefix='.sales-stage53-',delete=False) as f:f.write(data);tmp=f.name
   os.chmod(tmp,(stat.st_mode & 0o777) if row['backup'] else 0o644);os.chown(tmp,stat.st_uid,stat.st_gid)
   os.replace(tmp,str(path));written.append(row)
  checks=[]
  for route in ('/','/order/','/category/kompyuternye-kresla/','/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','/stul-sevyn-bukle-kofeynyy-2-sht/'):
   p=subprocess.run(['curl','-ksSL','--max-time','40','-w','\n%{http_code}','https://profikompany.ru'+route+'?sales53='+str(os.getpid())],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=47)
   html,status=p.stdout.rsplit('\n',1) if '\n' in p.stdout else ('','')
   ok=p.returncode==0 and status=='200' and 'montserrat.megapolis53.css' in html and 'megapolis-funnel-v2.js' in html and 'id="callback-modal"' not in html and 'Заказать звонок' not in html and '2449264' in html and '6776332' in html
   if route=='/':ok=ok and 'id="megapolis-home-search"' in html and 'megapolis-mobile-categories__grid' in html
   if route.endswith('chernaya/'):ok=ok and 'megapolis-buy-to-cart-v1.js' in html and 'id="megapolis-mobile-buy"' in html
   checks.append({'route':route,'status':status,'ok':ok})
  if not all(x['ok'] for x in checks):raise RuntimeError('Smoke failed '+json.dumps(checks))
  print('STAGE53_DEPLOY='+json.dumps({'success':True,'backup':str(folder),'files':len(records),'font_css_old_bytes':len(original_font.encode()),'font_css_new_bytes':len(css.encode()),'font_files':len(fonts),'checks':checks},ensure_ascii=False),flush=True)
 except Exception as ex:
  for row in reversed(written):
   path=root/row['path']
   if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=row['new_sha']:raise RuntimeError('Concurrent edit blocks rollback: '+row['path'])
   if row['backup']:shutil.copy2(str(folder/row['backup']),str(path))
   elif path.exists():path.unlink()
  print('STAGE53_ROLLBACK='+json.dumps({'error':str(ex),'restored':len(written)}),flush=True)
  raise
PY
'''

def rollback_remote(folder):
    if not folder.startswith('/home/web/vm-23f9aff9.na4u.ru/storefront-backups/stage53-sales-'):
        raise ValueError('Invalid backup folder')
    return '''set -eu
python3 - <<'PY'
import hashlib,json,pathlib,shutil
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
folder=pathlib.Path('''+repr(folder)+''')
rows=json.loads((folder/'manifest.json').read_text())
for row in rows:
 p=root/row['path']
 if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest()!=row['new_sha']:raise RuntimeError('Live file differs, abort rollback: '+row['path'])
for row in reversed(rows):
 p=root/row['path']
 if row['backup']:shutil.copy2(str(folder/row['backup']),str(p))
 else:p.unlink()
print('STAGE53_ROLLBACK='+json.dumps({'restored':len(rows)}),flush=True)
PY
'''

def main():
    state=Path('stage53-sales-state.json')
    if '--rollback' in sys.argv:
        if not state.exists():return
        out=execute(rollback_remote(json.loads(state.read_text())['backup']))
        for line in out.splitlines():
            if line.startswith('STAGE53_'):print(line,flush=True)
        state.unlink();return
    modules={name:server_module((BASE/name).read_text()) for name in ('stage53_mobile_performance.py','stage54_product_filters.py')}
    assets={}
    for target,filename in [('css/megapolis-mobile-home.css','megapolis-mobile-home.css'),('js/megapolis-mobile-home.js','megapolis-mobile-home.js'),('js/megapolis-funnel-v2.js','megapolis-funnel-v2.js'),('css/megapolis-product-filter-v1.css','megapolis-product-filter-v1.css'),('js/megapolis-product-filter-v1.js','megapolis-product-filter-v1.js')]:
        apps=('site','shop') if 'product-filter' in filename else ('site',)
        for app in apps:assets['wa-data/public/'+app+'/themes/pureMegapolis42/'+target]=(BASE/'assets'/filename).read_text()
    payload=base64.b64encode(json.dumps({'modules':modules,'assets':assets,'audited':AUDITED}).encode()).decode()
    out=execute(REMOTE.replace('PAYLOAD_B64',payload))
    for line in out.splitlines():
        if line.startswith('STAGE53_'):
            print(line,flush=True)
            if line.startswith('STAGE53_DEPLOY='):
                data=json.loads(line.split('=',1)[1]);state.write_text(json.dumps({'backup':data['backup']}))
    if not state.exists():raise RuntimeError('Deployment result absent')

if __name__=='__main__':main()
