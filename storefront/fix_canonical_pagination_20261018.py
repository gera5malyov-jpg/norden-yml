#!/usr/bin/env python3
"""Safe canonical pagination fix scoped to profikompany.ru, rollback on failed QA."""
import json
from stage52_disable_callback import execute

REMOTE = r'''set -eu
python3 - <<'PY'
import datetime,hashlib,json,os,pathlib,re,shutil,subprocess,tempfile
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
file=root/'wa-data/public/site/themes/pureMegapolis42/index.html'
base='https://profikompany.ru'
expected_sha='87c29bf35cce60142392c3f831f040bbbbdb6e4e9c8879584eaf17d092b7ffff'
old_block="""    {* CANONICAL *}
    {if !empty($canonical)}
      <link rel="canonical" href="{$canonical|escape}" />
    {/if}"""
new_block="""    {* CANONICAL *}
    {* Only clean numbered category pages have their own canonical. *}
    {if $wa->domainUrl() == 'https://profikompany.ru' && $wa->param('action') == 'category' && $wa->get('page', 0) > 1 && count($wa->get()) == 1}
      <link rel="canonical" href="{$wa->currentUrl(true, true)|escape}?page={$wa->get('page', 0)|intval}" />
    {elseif !empty($canonical)}
      <link rel="canonical" href="{$canonical|escape}" />
    {/if}"""
def sha(b):return hashlib.sha256(b).hexdigest()
def get(route):
 p=subprocess.Popen(['curl','-ksSL','--max-time','30','-w','\n%{http_code}',base+route],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 out,err=p.communicate(timeout=35)
 body,status=out.rsplit('\n',1) if '\n' in out else ('','')
 head=body.split('</head>',1)[0]
 c=re.findall(r'<link[^>]*rel="canonical"[^>]*href="([^"]+)"',head,re.I)
 robots=re.findall(r'<meta[^>]*name="robots"[^>]*>',head,re.I)
 ids=list(dict.fromkeys(re.findall(r'data-product-id="([0-9]+)"',body)))
 return {'status':status,'canonical':c,'robots':robots,'ids':ids,'rc':p.returncode,'error':err[-100:]}
# Preflight 1 - exact live source and no previous pagination patch.
old=file.read_bytes();source=old.decode('utf-8')
if sha(old)!=expected_sha or source.count(old_block)!=1 or 'Only clean numbered category pages' in source or '$_seo_pagination_number' in source:
 raise RuntimeError('PREFLIGHT 1: live template hash/anchor differ or already patched')
new=source.replace(old_block,new_block,1).encode('utf-8')
if new.count(b'<link rel="canonical"')!=old.count(b'<link rel="canonical"')+1:
 raise RuntimeError('PREFLIGHT 1: unexpected canonical template delta')
print('CANONICAL_PREFLIGHT='+json.dumps({'number':1,'ok':True,'source_sha':sha(old)}),flush=True)
# Preflight 2 - distinct products and existing base canonicals.
p1=get('/category/kompyuternye-kresla/')
p2=get('/category/kompyuternye-kresla/?page=2')
main=base+'/category/kompyuternye-kresla/'
if any(p['rc']!=0 or p['status']!='200' or p['canonical']!=[main] or any('noindex' in s.lower() for s in p['robots']) for p in [p1,p2]):
 raise RuntimeError('PREFLIGHT 2: live canonical/indexability changed')
if len(p1['ids'])<10 or len(p2['ids'])<10 or set(p1['ids'])==set(p2['ids']):
 raise RuntimeError('PREFLIGHT 2: no distinct paginated catalog products')
print('CANONICAL_PREFLIGHT='+json.dumps({'number':2,'ok':True,'first_page_products':len(p1['ids']),'second_page_products':len(p2['ids'])}),flush=True)
# Preflight 3 - sorting and product checkout controls must be preserved.
sort=get('/category/kompyuternye-kresla/?page=2&sort=price')
product=get('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/')
if sort['status']!='200' or sort['canonical']!=[main] or product['status']!='200' or product['canonical']!=[base+'/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/']:
 raise RuntimeError('PREFLIGHT 3: sort/product control mismatch')
print('CANONICAL_PREFLIGHT='+json.dumps({'number':3,'ok':True,'sort_isolated':True,'product_unchanged':True}),flush=True)
# Back up before touching active template; refuse concurrent write.
folder=root.parent/'storefront-backups'/('canonical-pagination-20261018-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700,parents=True,exist_ok=False)
backup=folder/'index.html'
shutil.copy2(str(file),str(backup));os.chmod(str(backup),0o600)
if sha(backup.read_bytes())!=expected_sha or sha(file.read_bytes())!=expected_sha:
 raise RuntimeError('Concurrent change or invalid backup')
st=file.stat();tmp=None;written=False
try:
 with tempfile.NamedTemporaryFile('wb',dir=str(file.parent),prefix='.canonical-page-safe-',delete=False) as f:
  f.write(new);tmp=f.name
 os.chmod(tmp,st.st_mode&0o777);os.chown(tmp,st.st_uid,st.st_gid)
 if sha(file.read_bytes())!=expected_sha:raise RuntimeError('Concurrent template edit')
 os.replace(tmp,str(file));written=True;tmp=None
 # Refresh compiled Smarty cache by giving the theme file a new mtime.
 os.utime(str(file),None)
 cases=[
  ('/',base+'/'),
  ('/category/kompyuternye-kresla/',main),
  ('/category/kompyuternye-kresla/?page=2',main+'?page=2'),
  ('/category/kompyuternye-kresla/?page=3',main+'?page=3'),
  ('/category/kompyuternye-kresla/?page=4',main+'?page=4'),
  ('/category/stoly_1/stoly-pismennye/?page=2',base+'/category/stoly_1/stoly-pismennye/?page=2'),
  ('/category/kompyuternye-kresla/kresla-rukovoditelya/?page=2',base+'/category/kompyuternye-kresla/kresla-rukovoditelya/?page=2'),
  ('/category/kompyuternye-kresla/?sort=price',main),
  ('/category/kompyuternye-kresla/?page=2&sort=price',main),
  ('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/',base+'/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/')
 ]
 if len(cases)!=10:raise RuntimeError('Acceptance plan count differs from ten')
 for n,(route,expected) in enumerate(cases,1):
  result=get(route)
  ok=result['rc']==0 and result['status']=='200' and result['canonical']==[expected] and not any('noindex' in r.lower() for r in result['robots'])
  print('CANONICAL_CHECK='+json.dumps({'number':n,'route':route,'http':result['status'],'canonical':result['canonical'],'ok':ok},ensure_ascii=False),flush=True)
  if not ok:raise RuntimeError('Acceptance failed '+str(n)+': '+json.dumps(result,ensure_ascii=False)[:400])
 if sha(file.read_bytes())!=sha(new):raise RuntimeError('Source changed during validation')
 print('CANONICAL_DEPLOY='+json.dumps({'ok':True,'backup':str(backup),'file':'site/themes/pureMegapolis42/index.html','tests_passed':10,'tests_total':10},ensure_ascii=False),flush=True)
except Exception as e:
 if written:
  if sha(file.read_bytes())==sha(new):
   shutil.copy2(str(backup),str(file));os.utime(str(file),None)
   print('CANONICAL_ROLLBACK='+json.dumps({'restored':True,'reason':str(e)[:200]},ensure_ascii=False),flush=True)
  else:
   print('CANONICAL_ROLLBACK='+json.dumps({'restored':False,'reason':'concurrent modification, manual review needed','backup':str(backup)},ensure_ascii=False),flush=True)
 elif tmp:
  os.unlink(tmp)
 raise
PY
'''
if __name__ == '__main__':
    output=execute(REMOTE)
    for line in output.splitlines():
        if line.startswith(('CANONICAL_PREFLIGHT=','CANONICAL_CHECK=','CANONICAL_DEPLOY=','CANONICAL_ROLLBACK=')):
            print(line,flush=True)
    if 'CANONICAL_DEPLOY=' not in output:
        raise RuntimeError('Canonical deployment not verified')
