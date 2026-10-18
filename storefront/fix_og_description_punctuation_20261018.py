#!/usr/bin/env python3
"""Minimal Open Graph punctuation correction; leave existing SEO title and canonical logic unchanged."""
from stage52_disable_callback import execute
import json

REMOTE = r"""set -eu
python3 - <<'PY'
import datetime
import hashlib
import html
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time

root = pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
file = root / 'wa-data/public/site/themes/pureMegapolis42/index.html'
domain = 'https://profikompany.ru'
expected_sha = '0ec8b44f3cfcb68a9f34d633e759fa4110e0751b0874b196d6c589418c8e4c7b'
old_code = '''      {$_megapolis_head|replace:$_megapolis_old_tag:$_megapolis_new_tag}
    {else}
      {$wa->head()}
    {/if}'''
new_code = '''      {* Keep the previously validated page-specific canonical, clean OG punctuation. *}
      {$_megapolis_head|replace:$_megapolis_old_tag:$_megapolis_new_tag|replace:'руб..':'руб.'}
    {else}
      {if $wa->domainUrl() == 'https://profikompany.ru' && ($wa->param('action') == 'category' || $wa->param('action') == 'product')}
        {$_megapolis_head = $wa->head()}
        {$_megapolis_head|replace:'руб..':'руб.'}
      {else}
        {$wa->head()}
      {/if}
    {/if}'''
cases = [
  ('/', 'home',domain+'/'),
  ('/category/kompyuternye-kresla/', 'category',domain+'/category/kompyuternye-kresla/'),
  ('/category/kompyuternye-kresla/?page=2', 'category',domain+'/category/kompyuternye-kresla/?page=2'),
  ('/category/kompyuternye-kresla/?page=3', 'category',domain+'/category/kompyuternye-kresla/?page=3'),
  ('/category/kompyuternye-kresla/?sort=price&order=asc','category',domain+'/category/kompyuternye-kresla/'),
  ('/category/kompyuternye-kresla/?page=2&sort=price','category',domain+'/category/kompyuternye-kresla/'),
  ('/category/stoly_1/stoly-pismennye/?page=2','category',domain+'/category/stoly_1/stoly-pismennye/?page=2'),
  ('/category/kompyuternye-kresla/kresla-rukovoditelya/?page=2','category',domain+'/category/kompyuternye-kresla/kresla-rukovoditelya/?page=2'),
  ('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','product',domain+'/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/'),
  ('/garantiya/', 'info',None),
]

def sha(data): return hashlib.sha256(data).hexdigest()

def attributes(tag):
    return dict((key.lower(),html.unescape(value)) for key,quote,value in re.findall(r'([a-zA-Z_:.-]+)\s*=\s*(["\'])(.*?)\2',tag,re.S))

def request(route):
    p=subprocess.Popen(['curl','-fsSL','--compressed','--max-time','30','-w','\n%{http_code}',domain+route],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
    body,err=p.communicate(timeout=38)
    page,status=body.rsplit('\n',1) if '\n' in body else ('','')
    head=page.split('</head>',1)[0]
    titles=[html.unescape(x) for x in re.findall(r'<title[^>]*>(.*?)</title>',head,re.I|re.S)]
    desc=[attributes(t).get('content','') for t in re.findall(r'<meta\b[^>]*>',head,re.I) if attributes(t).get('name','').lower() == 'description']
    ogdesc=[attributes(t).get('content','') for t in re.findall(r'<meta\b[^>]*>',head,re.I) if attributes(t).get('property','').lower() == 'og:description']
    canon=[attributes(t).get('href','') for t in re.findall(r'<link\b[^>]*>',head,re.I) if attributes(t).get('rel','').lower() == 'canonical']
    return {'status':status,'rc':p.returncode,'title':titles,'desc':desc,'ogdesc':ogdesc,'canonical':canon,
            'product_count':len(set(re.findall(r'data-product-id="(\d+)',page))), 'length':len(page)}

# Precheck 1 - safeguard previously deployed theme changes.
before=file.read_bytes()
source=before.decode('utf-8')
if sha(before)!=expected_sha or source.count(old_code)!=1 or 'Keep the previously validated page-specific canonical' in source:
    raise RuntimeError('PREFLIGHT 1: unexpected theme content; no edit')
modified=source.replace(old_code,new_code,1).encode('utf-8')
print('META_PREFLIGHT='+json.dumps({'check':1,'ok':True,'source_sha256':sha(before)}),flush=True)

# Precheck 2 - all ten live requests must pass, with one title and one meta description.
baseline=[]
for route,kind,canonical in cases:
    item=request(route)
    if item['rc'] or item['status']!='200' or len(item['title'])!=1 or len(item['desc'])!=1 or (canonical and item['canonical']!=[canonical]):
        raise RuntimeError('PREFLIGHT 2: '+route+': '+json.dumps(item,ensure_ascii=False)[:420])
    baseline.append(item)
if not all(len(baseline[i]['ogdesc'])==1 for i,(_,kind,_) in enumerate(cases) if kind in ('product','category')):
    raise RuntimeError('PREFLIGHT 2: unexpected Open Graph emitter')
print('META_PREFLIGHT='+json.dumps({'check':2,'ok':True,'routes':len(baseline)}),flush=True)

# Precheck 3 - verify bug and existing unique numbered titles without any write.
for i in (1,2,3,6,7,8):
    if 'руб..' not in baseline[i]['ogdesc'][0] or 'руб..' in baseline[i]['desc'][0]:
        raise RuntimeError('PREFLIGHT 3: expected OG-only typo not reproducible: '+cases[i][0])
if baseline[1]['title']==baseline[2]['title'] or baseline[2]['title']==baseline[3]['title']:
    raise RuntimeError('PREFLIGHT 3: paginated Title uniqueness was lost')
print('META_PREFLIGHT='+json.dumps({'check':3,'ok':True,'og_typo_confirmed':True,'paginated_titles_already_unique':True}),flush=True)

folder=root.parent/'storefront-backups'/('seo-og-rub-20261018-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700,parents=True)
backup=folder/'index.html'
shutil.copy2(str(file),str(backup))
os.chmod(str(backup),0o600)
if sha(backup.read_bytes())!=sha(before) or sha(file.read_bytes())!=sha(before):
    raise RuntimeError('Backup or concurrent change verification failed')
print('META_BACKUP='+json.dumps({'file':str(backup),'sha256':sha(before)}),flush=True)

tmp=None
written=False
try:
    stat=file.stat()
    with tempfile.NamedTemporaryFile('wb',dir=str(file.parent),prefix='.seo-og-safe-',delete=False) as fp:
        fp.write(modified)
        tmp=fp.name
    os.chmod(tmp,stat.st_mode & 0o777)
    os.chown(tmp,stat.st_uid,stat.st_gid)
    if sha(file.read_bytes())!=sha(before): raise RuntimeError('Concurrent source change; cancel')
    os.replace(tmp,str(file))
    tmp=None
    written=True
    os.utime(str(file),None)
    for i,(route,kind,canonical) in enumerate(cases):
        after=request(route)
        b=baseline[i]
        expected_og=[v.replace('руб..','руб.') for v in b['ogdesc']] if kind in ('product','category') else b['ogdesc']
        ok=all([
            after['rc']==0,after['status']=='200',
            after['title']==b['title'],after['desc']==b['desc'],
            after['canonical']==b['canonical'],
            after['ogdesc']==expected_og,
            after['product_count']==b['product_count'],
            len(after['ogdesc'])==len(b['ogdesc']), len(after['desc'])==1, len(after['title'])==1
        ])
        print('META_ACCEPTANCE='+json.dumps({'case':i+1,'route':route,'ok':ok,'status':after['status'],
          'title_unchanged':after['title']==b['title'],
          'description_unchanged':after['desc']==b['desc'],
          'canonical_unchanged':after['canonical']==b['canonical'],
          'og_correct':after['ogdesc']==expected_og},ensure_ascii=False),flush=True)
        if not ok:
            raise RuntimeError('Acceptance case failed: '+str(i+1)+' '+json.dumps(after,ensure_ascii=False)[:420])
    if sha(file.read_bytes())!=sha(modified):raise RuntimeError('Changed during acceptance')
    print('META_DEPLOY='+json.dumps({'ok':True,'backup':str(backup),'file':str(file.relative_to(root)),'prechecks':3,'postchecks':10,'unchanged':'Title, meta description, canonical, listing product counts, catalog database, prices, stocks, images, YML Pro'},ensure_ascii=False),flush=True)
except Exception as e:
    if written and sha(file.read_bytes())==sha(modified):
        shutil.copy2(str(backup),str(file))
        os.utime(str(file),None)
        print('META_ROLLBACK='+json.dumps({'restored':True,'reason':str(e)[:300]},ensure_ascii=False),flush=True)
    elif written:
        print('META_ROLLBACK='+json.dumps({'restored':False,'backup':str(backup),'reason':'Concurrent modification; manual review required'},ensure_ascii=False),flush=True)
    if tmp and os.path.isfile(tmp):os.unlink(tmp)
    raise
PY
"""

if __name__ == '__main__':
    result=execute(REMOTE)
    for line in result.splitlines():
        if line.startswith(('META_PREFLIGHT=','META_BACKUP=','META_ACCEPTANCE=','META_DEPLOY=','META_ROLLBACK=')):
            print(line,flush=True)
    if 'META_DEPLOY=' not in result:
        raise RuntimeError('OG metadata live deployment not verified')
