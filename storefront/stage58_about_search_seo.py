#!/usr/bin/env python3
"""Guarded, site-scoped SEO repair: company metadata and noindex for internal search."""
from __future__ import print_function
from stage52_disable_callback import execute

REMOTE = r'''set -eu
python3 - <<'PY'
from __future__ import print_function
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

root = pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
target = root / 'wa-data/public/site/themes/pureMegapolis42/index.html'
domain = 'https://profikompany.ru'
title = 'О компании «Мегаполис» | Мебель для дома и бизнеса'
description = 'О компании «Мегаполис»: мебель, предметы интерьера и освещение для дома, офиса и бизнеса. Ассортимент, принципы работы и организация доставки по России.'

info_anchor = """      {if $_seo_info_url == '/privacy-policy/'}"""
info_patch = """      {* SEO: about page, based on its existing public content. *}
      {if $_seo_info_url == '/o-kompanii/'}
        {$_info_title = 'О компании «Мегаполис» | Мебель для дома и бизнеса'}
        {$_info_description = 'О компании «Мегаполис»: мебель, предметы интерьера и освещение для дома, офиса и бизнеса. Ассортимент, принципы работы и организация доставки по России.'}
      {/if}
"""
canonical_anchor = '    {* CANONICAL *}'
canonical_patch = """    {* CANONICAL *}
    {* Provide one self-canonical for the existing About page. *}
    {if $wa->domainUrl() == 'https://profikompany.ru' && $_seo_info_url == '/o-kompanii/'}
      <link rel="canonical" href="https://profikompany.ru/o-kompanii/" />
    {/if}"""
meta_anchor = '    <meta name="viewport" content="width=device-width, initial-scale=1" />'
search_patch = """    {* Scoped noindex: internal search results, keep crawling product links. *}
    {if $wa->domainUrl() == 'https://profikompany.ru' && $wa->currentUrl(false, true) == '/search/'}
      <meta name="robots" content="noindex,follow" />
    {/if}
"""
routes = [
    ('/o-kompanii/', 'about'),
    ('/search/?query=%D0%91%D0%B8%D1%81%D1%82%D1%80%D0%BE', 'search'),
    ('/search/?page=2&query=%D0%91%D0%B8%D1%81%D1%82%D1%80%D0%BE', 'search'),
    ('/search/?query=abcdef123123nothere', 'search'),
    ('/', 'control'),
    ('/category/kompyuternye-kresla/', 'control'),
    ('/category/kompyuternye-kresla/?page=2', 'control'),
    ('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/', 'control'),
    ('/oplata/', 'control'),
    ('/garantiya/', 'control'),
]
def sha(blob):return hashlib.sha256(blob).hexdigest()
def attributes(tag):
    return dict((key.lower(),html.unescape(value)) for key,_,value in re.findall(r'([a-zA-Z_:.-]+)\s*=\s*(["\'])(.*?)\2',tag,re.S))
def request(path):
    proc=subprocess.Popen(['curl','-ksSL','--compressed','--max-time','35','-w','\n%{http_code}',domain+path],
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
    result,error=proc.communicate(timeout=43)
    page,status=result.rsplit('\n',1) if '\n' in result else ('','')
    head=page.split('</head>',1)[0]
    titles=[html.unescape(x).strip() for x in re.findall(r'<title[^>]*>(.*?)</title>',head,re.I|re.S)]
    metas=[attributes(tag) for tag in re.findall(r'<meta\b[^>]*>',head,re.I)]
    links=[attributes(tag) for tag in re.findall(r'<link\b[^>]*>',head,re.I)]
    robots=[m.get('content','') for m in metas if m.get('name','').lower() in ('robots','yandex')]
    descriptions=[m.get('content','').strip() for m in metas if m.get('name','').lower()=='description']
    canonical=[a.get('href','') for a in links if a.get('rel','').lower()=='canonical']
    prod=set(re.findall(r'data-product-id=["\'](\d+)',page,re.I))
    return {'status':status,'rc':proc.returncode,'title':titles,'description':descriptions,
            'canonical':canonical,'robots':robots,'products':len(prod),
            'has_about_h1':bool(re.search(r'<h1\b[^>]*>[^<]*О Компании',page,re.I)),
            'has_phones':'+7 (812)' in page and '+7 (499)' in page}

# Precheck 1: exact, unique anchors; preserve existing canonical/pagination/OG logic.
original=target.read_bytes()
source=original.decode('utf-8')
required=['megapolis-native-category-meta-stage55','Keep the previously validated page-specific canonical',
          'Scoped SEO improvements','$_seo_info_url','$_info_description']
if any(x not in source for x in required):
    raise RuntimeError('PREFLIGHT 1: previously validated storefront template markers missing')
if source.count(info_anchor)!=1 or source.count(canonical_anchor)!=1 or source.count(meta_anchor)!=1:
    raise RuntimeError('PREFLIGHT 1: changed or ambiguous theme SEO anchors')
if "Scoped noindex: internal search results" in source or "SEO: about page, based on its existing public content." in source:
    raise RuntimeError('PREFLIGHT 1: changes already present, refusing to repeat')
patched=source.replace(info_anchor,info_patch+info_anchor,1)
patched=patched.replace(canonical_anchor,canonical_patch,1)
patched=patched.replace(meta_anchor,meta_anchor+'\n'+search_patch,1)
if patched==source:raise RuntimeError('PREFLIGHT 1: no changes generated')
new=patched.encode('utf-8')
print('STAGE58_PREFLIGHT='+json.dumps({'check':1,'ok':True,'sha256':sha(original),
 'planned_files':['wa-data/public/site/themes/pureMegapolis42/index.html']}),flush=True)

# Precheck 2: all ten routes serve normally and existing shop SEO remains sound.
baseline=[]
for path,kind in routes:
    result=request(path)
    if result['rc']!=0 or result['status']!='200' or len(result['title'])!=1 or len(result['description'])!=1:
        raise RuntimeError('PREFLIGHT 2: unexpected live HTML '+path+' '+json.dumps(result,ensure_ascii=False)[:450])
    baseline.append(result)
if baseline[5]['canonical']!=[domain+'/category/kompyuternye-kresla/']:
    raise RuntimeError('PREFLIGHT 2: category canonical changed')
if baseline[6]['canonical']!=[domain+'/category/kompyuternye-kresla/?page=2']:
    raise RuntimeError('PREFLIGHT 2: pagination canonical changed')
if baseline[7]['canonical']!=[domain+'/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/']:
    raise RuntimeError('PREFLIGHT 2: product canonical changed')
print('STAGE58_PREFLIGHT='+json.dumps({'check':2,'ok':True,'routes':len(routes)}),flush=True)

# Precheck 3: confirm exact two faults and preserve search product listings.
if baseline[0]['title'][0] not in ('— Мегаполис','– Мегаполис','- Мегаполис') and baseline[0]['title'][0].strip(' —–-')!='Мегаполис':
    raise RuntimeError('PREFLIGHT 3: About title already modified by another workflow')
if baseline[0]['canonical'] or not baseline[0]['has_about_h1']:
    raise RuntimeError('PREFLIGHT 3: About route canonical/H1 differs')
if baseline[0]['description'][0].startswith('О компании'):
    raise RuntimeError('PREFLIGHT 3: About description already repaired')
if any(('noindex' in ','.join(baseline[i]['robots']).lower()) for i in (1,2,3)):
    raise RuntimeError('PREFLIGHT 3: Search noindex is already set')
if baseline[1]['products']==0 or baseline[2]['products']==0:
    raise RuntimeError('PREFLIGHT 3: Search result products unavailable')
print('STAGE58_PREFLIGHT='+json.dumps({'check':3,'ok':True,'about_metadata_missing':True,'search_noindex_missing':True}),flush=True)

# Persistent private backup, verify checksum, no database writes.
folder=root.parent/'storefront-backups'/('stage58-about-search-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700,parents=True)
backup=folder/'index.html'
shutil.copy2(str(target),str(backup));os.chmod(str(backup),0o600)
if sha(backup.read_bytes())!=sha(original) or sha(target.read_bytes())!=sha(original):
    raise RuntimeError('Backup mismatch or concurrent modification')
print('STAGE58_BACKUP='+json.dumps({'file':str(backup),'sha256':sha(original)}),flush=True)
temporary=None
written=False
try:
    stat=target.stat()
    with tempfile.NamedTemporaryFile('wb',dir=str(target.parent),prefix='.stage58-seo-',delete=False) as f:
        f.write(new)
        temporary=f.name
    os.chmod(temporary,stat.st_mode & 0o777)
    os.chown(temporary,stat.st_uid,stat.st_gid)
    if sha(target.read_bytes())!=sha(original):raise RuntimeError('Source changed immediately before deploy')
    os.replace(temporary,str(target));temporary=None
    written=True
    os.utime(str(target),None)
    for index,(path,kind) in enumerate(routes):
        after=request(path)
        before=baseline[index]
        base_ok=after['rc']==0 and after['status']=='200' and after['products']==before['products'] and after['has_phones']==before['has_phones']
        if kind=='about':
            ok=base_ok and after['title']==[title] and after['description']==[description] and after['canonical']==[domain+'/o-kompanii/'] and after['has_about_h1'] and after['robots']==before['robots']
        elif kind=='search':
            rtags=[v.lower().replace(' ','') for v in after['robots']]
            ok=base_ok and after['title']==before['title'] and after['description']==before['description'] and after['canonical']==before['canonical'] and rtags==['noindex,follow']
        else:
            ok=base_ok and after['title']==before['title'] and after['description']==before['description'] and after['canonical']==before['canonical'] and after['robots']==before['robots']
        print('STAGE58_TEST='+json.dumps({'number':index+1,'route':path,'ok':bool(ok),'http':after['status'],
                'same_product_count':after['products']==before['products'],'canonical':after['canonical'],
                'robots':after['robots']},ensure_ascii=False),flush=True)
        if not ok:
            raise RuntimeError('Live acceptance '+str(index+1)+' failed: '+json.dumps({'before':before,'after':after},ensure_ascii=False)[:620])
    if sha(target.read_bytes())!=sha(new):
        raise RuntimeError('Template changed during acceptance')
    print('STAGE58_DEPLOY='+json.dumps({'ok':True,'tests_passed':10,'tests_total':10,'prechecks':3,
        'backup':str(backup),'file':str(target.relative_to(root)),'catalog_write':False,'robots_txt_unchanged':True,
        'search_results_preserved':True},ensure_ascii=False),flush=True)
except Exception as error:
    if written and sha(target.read_bytes())==sha(new):
        shutil.copy2(str(backup),str(target))
        os.utime(str(target),None)
        print('STAGE58_ROLLBACK='+json.dumps({'restored':True,'reason':str(error)[:520]},ensure_ascii=False),flush=True)
    elif written:
        print('STAGE58_ROLLBACK='+json.dumps({'restored':False,'backup':str(backup),
            'reason':'The live file was concurrently modified; refused to overwrite that change'},ensure_ascii=False),flush=True)
    if temporary and os.path.isfile(temporary):os.unlink(temporary)
    raise
PY
'''

if __name__ == '__main__':
    response=execute(REMOTE)
    for line in response.splitlines():
        if line.startswith(('STAGE58_PREFLIGHT=','STAGE58_BACKUP=','STAGE58_TEST=','STAGE58_DEPLOY=','STAGE58_ROLLBACK=')):
            print(line,flush=True)
    if 'STAGE58_DEPLOY=' not in response:
        raise RuntimeError('No verified stage58 deployment result')
