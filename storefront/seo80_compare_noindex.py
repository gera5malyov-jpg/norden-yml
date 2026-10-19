#!/usr/bin/env python3
"""Guarded SEO fix: noindex,follow comparison routes, preserve live comparison links."""
from stage52_disable_callback import execute

REMOTE=r'''set -eu
python3 - <<'PY'
import pathlib,subprocess,re,json,hashlib,os,shutil,datetime,tempfile
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
file=root/'wa-data/public/site/themes/pureMegapolis42/index.html'
marker='megapolis-compare-noindex-20261019'
old="""    {* Scoped noindex: internal search results, keep crawling product links. *}
    {if $wa->domainUrl() == 'https://profikompany.ru' && $wa->currentUrl(false, true) == '/search/'}
      <meta name="robots" content="noindex,follow" />
    {/if}"""
new=old+"""
    {* megapolis-compare-noindex-20261019: comparison is useful to buyers but technical for SEO. *}
    {if $wa->domainUrl() == 'https://profikompany.ru'}
      {if $wa->currentUrl(false, true)|substr:0:9 == '/compare/'}
        <meta name="robots" content="noindex,follow" />
      {/if}
    {/if}"""
def check(path):
 p=subprocess.Popen(['curl','-ksSL','--compressed','--max-time','26','-w','\n%{http_code}','https://profikompany.ru'+path],
 stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 data,err=p.communicate(timeout=35)
 if '\n' not in data:return {'status':'','rc':p.returncode}
 html,status=data.rsplit('\n',1)
 head=html.split('</head>',1)[0]
 return {'rc':p.returncode,'status':status,
  'title':re.findall(r'<title[^>]*>(.*?)</title>',head,re.I|re.S),
  'description':re.findall(r'<meta[^>]+name=["\']description["\'][^>]*>',head,re.I),
  'canonical':re.findall(r'<link[^>]+rel=["\']canonical["\'][^>]*>',head,re.I),
  'robots':re.findall(r'<meta[^>]+name=["\']robots["\'][^>]*>',head,re.I),
  'compare_links':len(re.findall(r'<a\b[^>]+href=["\'](/compare/[^"\']*)["\']',html,re.I)),
  'comparison_item':'Стул для посетителей РС42' in html,
  'other_icons':'#icon-chart' in html,
  'fa_lazymarker':'megapolis-fa-on-demand-20261019' in head,
  'font76':'montserrat.megapolis76.css' in html,
  'has_cta':'class="product-add"' in html}
source=file.read_bytes();src=source.decode('utf8')
if src.count(old)!=1 or marker in src or 'megapolis-fa-on-demand-20261019' not in src:
 raise RuntimeError('PREFLIGHT 1 failed: unexpected theme or duplicate changes')
print('SEO80_PREFLIGHT='+json.dumps({'check':1,'ok':True,'sha':hashlib.sha256(source).hexdigest()}),flush=True)
routes=[
 ('/compare/','compare'),('/compare/1186404,1186400/','compare'),
 ('/compare/1186404/','compare'),('/compare/?products=1186404,1186400','compare'),
 ('/compare/?view=list','compare'),
 ('/','control'),('/category/kompyuternye-kresla/','control'),
 ('/category/kompyuternye-kresla/?page=2','control'),
 ('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','control'),
 ('/stul-prozrachnyy-pedrali-queen/','control'),
 ('/search/?query=%D0%91%D0%B8%D1%81%D1%82%D1%80%D0%BE','search'),
 ('/cart/','control'),('/order/','control'),('/oplata/','control')]
baseline=[check(route) for route,kind in routes]
if not all(x['status']=='200' and x['rc']==0 and x['title'] and x['fa_lazymarker'] for x in baseline):
 raise RuntimeError('PREFLIGHT 2: baseline status, metadata or previous optimization changed')
if any(x['robots'] for x in baseline[:5]):raise RuntimeError('PREFLIGHT 2: compare already has robot flags')
if not baseline[1]['comparison_item'] or baseline[8]['compare_links']==0 or baseline[6]['compare_links']==0:
 raise RuntimeError('PREFLIGHT 2: comparison items or internal links not found')
print('SEO80_PREFLIGHT='+json.dumps({'check':2,'ok':True,'routes':len(routes),
 'compare_hrefs_category':baseline[6]['compare_links'],'compare_hrefs_product':baseline[8]['compare_links']}),flush=True)
if not baseline[10]['robots'] or 'noindex' not in ','.join(baseline[10]['robots']).lower():
 raise RuntimeError('PREFLIGHT 3: existing search noindex missing')
if not baseline[1]['other_icons'] or not baseline[6]['other_icons']:
 raise RuntimeError('PREFLIGHT 3: comparison SVG icon missing')
print('SEO80_PREFLIGHT='+json.dumps({'check':3,'ok':True,'search_noindex_kept':True,'compare_sprite_kept':True}),flush=True)
folder=root.parent/'storefront-backups'/('seo80-compare-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(parents=True,mode=0o700)
backup=folder/'index.html'
shutil.copy2(str(file),str(backup));os.chmod(str(backup),0o600)
if backup.read_bytes()!=source:raise RuntimeError('Backup verification failed')
print('SEO80_BACKUP='+json.dumps({'file':str(backup)}),flush=True)
updated=src.replace(old,new,1).encode('utf8')
written=False
try:
 st=file.stat()
 with tempfile.NamedTemporaryFile('wb',dir=str(file.parent),prefix='.seo80-',delete=False) as tmp:
  tmp.write(updated);name=tmp.name
 os.chmod(name,st.st_mode&0o777);os.chown(name,st.st_uid,st.st_gid)
 if file.read_bytes()!=source:raise RuntimeError('Theme edited concurrently')
 os.rename(name,str(file));written=True
 for i,(route,kind) in enumerate(routes):
  a=check(route);b=baseline[i]
  common=(a['status']=='200' and a['rc']==0 and
    a['title']==b['title'] and a['canonical']==b['canonical'] and
    a['description']==b['description'] and a['compare_links']==b['compare_links'] and
    a['comparison_item']==b['comparison_item'] and a['other_icons']==b['other_icons'] and
    a['fa_lazymarker'] and a['font76']==b['font76'] and a['has_cta']==b['has_cta'])
  if kind=='compare':
   expected=len(a['robots'])==1 and 'noindex,follow' in a['robots'][0].lower()
  else:
   expected=a['robots']==b['robots']
  ok=bool(common and expected)
  print('SEO80_TEST='+json.dumps({'case':i+1,'route':route,'type':kind,'http':a['status'],'ok':ok,
    'noindex':bool(kind=='compare' and expected),'links_kept':a['compare_links']==b['compare_links']}),flush=True)
  if not ok:raise RuntimeError('Smoke check failed '+route+'; robots='+str(a['robots']))
 if file.read_bytes()!=updated:raise RuntimeError('Concurrent code change after deploy')
 print('SEO80_DEPLOY='+json.dumps({'success':True,'checks':len(routes),'compare_routes':5,
  'backup':str(backup),'robots_txt_untouched':True,'product_links_unchanged':True,
  'cart_and_compare_functionality_kept':True,'prices_stocks_yml_untouched':True}),flush=True)
except Exception as err:
 if written and file.read_bytes()==updated:
  shutil.copy2(str(backup),str(file))
  print('SEO80_ROLLBACK='+json.dumps({'restored':True,'reason':str(err)[:200]}),flush=True)
 raise
PY
'''
if __name__=='__main__':
 result=execute(REMOTE)
 for ln in result.splitlines():
  if ln.startswith('SEO80_'):print(ln,flush=True)
 if 'SEO80_DEPLOY=' not in result:
  raise RuntimeError('No verified compare SEO deployment result')
