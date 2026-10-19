#!/usr/bin/env python3
"""Scope only profikompany.ru mobile navigation: quick directions + accessible full catalog."""
from stage52_disable_callback import execute
REMOTE=r'''set -eu
python3 - <<'PY'
import pathlib,hashlib,datetime,shutil,tempfile,subprocess,re,json,os
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
file=root/'wa-data/public/site/themes/pureMegapolis42/header/header.mobile.html'
source=file.read_text('utf8')
marker='megapolis-mobile-catalog-shortcuts-20261019'
before_anchor='      <ul class="mobile-nav__list">\n        {foreach $_catalogs as $cat_item}'
after_anchor='      </ul>\n    {/if}\n\n    {* PAGES *}'
top=r"""      {if $wa->domainUrl() == 'https://profikompany.ru'}
      {* megapolis-mobile-catalog-shortcuts-20261019: shortcuts and full category tree, unchanged category data *}
      <section class="megapolis-menu-quick" aria-label="Основные разделы каталога">
        <div class="megapolis-menu-quick__title">Популярные разделы</div>
        <nav class="megapolis-menu-quick__links" aria-label="Основные направления">
          <a href="/category/kompyuternye-kresla/">Компьютерные кресла</a>
          <a href="/category/mebel-dlya-ofisa/">Офисная мебель</a>
          <a href="/category/stul/">Стулья</a>
          <a href="/category/stoly_1/">Столы</a>
          <a href="/category/shkafy/">Шкафы</a>
          <a href="/category/igrovye-stoly/">Игровые столы</a>
        </nav>
      </section>
      <details class="megapolis-menu-all">
        <summary>Все категории <span aria-hidden="true">⌄</span></summary>
      {/if}
"""
tail=r"""      {if $wa->domainUrl() == 'https://profikompany.ru'}
      </details>
      {literal}
      <style id="megapolis-mobile-catalog-style-20261019">
        .megapolis-menu-quick{padding:14px 16px 10px;border-bottom:1px solid #e7e4df}
        .megapolis-menu-quick__title{font-weight:700;font-size:15px;margin-bottom:10px;line-height:1.3}
        .megapolis-menu-quick__links{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
        .megapolis-menu-quick__links a{display:flex;align-items:center;min-height:43px;padding:8px 10px;border:1px solid #e6e3df;border-radius:6px;color:#302a26;text-decoration:none;font-size:13px;line-height:1.3;overflow-wrap:break-word}
        .megapolis-menu-quick__links a:focus-visible,.megapolis-menu-all summary:focus-visible{outline:2px solid #72553c;outline-offset:2px}
        .megapolis-menu-all{border-bottom:1px solid #e7e4df}
        .megapolis-menu-all>summary{display:flex;align-items:center;justify-content:space-between;cursor:pointer;padding:13px 16px;font-weight:700;list-style:none;font-size:15px}
        .megapolis-menu-all>summary::-webkit-details-marker{display:none}
        .megapolis-menu-all>summary span{display:inline-block;transition:transform .15s}
        .megapolis-menu-all[open]>summary span{transform:rotate(180deg)}
      </style>
      {/literal}
      {/if}
"""
def http(path):
 p=subprocess.Popen(['curl','-ksSL','--compressed','--max-time','31','-w','\n%{http_code}', 'https://profikompany.ru'+path],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 out,err=p.communicate(timeout=39)
 body,code=out.rsplit('\n',1) if '\n' in out else ('','')
 head=body.split('</head>',1)[0]
 return {'rc':p.returncode,'http':code,'title':re.findall(r'<title[^>]*>(.*?)</title>',head,re.I|re.S),
  'robots':re.findall(r'<meta\b[^>]*name=["\']robots["\'][^>]*>',head,re.I),
  'canonical':re.findall(r'<link\b[^>]*rel=["\']canonical["\'][^>]*>',head,re.I),
  'mobile_roots':len(re.findall(r'<a\b[^>]*class="mobile-nav__link"[^>]*>',body)),
  'mobile_subs':len(re.findall(r'<a\b[^>]*class="mobile-nav__subs-link"[^>]*>',body)),
  'old_category_urls':set(re.findall(r'<a\s+href="([^"]+)"\s+class="mobile-nav__(?:link|subs-link)"',body)),
  'summary': 'class="megapolis-menu-all"' in body,
  'quick_count':len(re.findall(r'<a href="/category/[^"]+"',body.split('megapolis-menu-quick__links"',1)[1].split('</nav>',1)[0])) if 'megapolis-menu-quick__links"' in body else 0,
  'buy':'id="s-product-form"' in body,
  'trust':'megapolis-purchase-trust' in body}
if source.count(before_anchor)!=1 or source.count(after_anchor)!=1 or marker in source or source.count('mobile-nav__subs-list collapse')<2:
 raise RuntimeError('PREFLIGHT 1 template changed')
print('NAV87_PRE='+json.dumps({'check':1,'ok':True,'sha256':hashlib.sha256(source.encode()).hexdigest()}),flush=True)
routes=['/','/category/kompyuternye-kresla/','/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','/category/stul/','/category/stoly_1/','/category/shkafy/','/category/mebel-dlya-ofisa/','/category/igrovye-stoly/','/compare/','/search/?query=stul','/oplata/']
baseline=[http(r) for r in routes]
if not all(x['http']=='200' and x['rc']==0 and len(x['title'])==1 and x['mobile_roots']>12 for x in baseline):
 raise RuntimeError('PREFLIGHT 2 routes or mobile menu changed')
print('NAV87_PRE='+json.dumps({'check':2,'ok':True,'routes':len(routes),'root_sections':baseline[0]['mobile_roots'],'submenu_links':baseline[0]['mobile_subs']}),flush=True)
if not any('noindex' in ''.join(x['robots']) for i,x in enumerate(baseline) if i in (8,9)):
 raise RuntimeError('PREFLIGHT 3 existing SEO control absent')
if not baseline[2]['buy'] or not baseline[2]['trust']:
 raise RuntimeError('PREFLIGHT 3 buy block or existing trust missing')
print('NAV87_PRE='+json.dumps({'check':3,'ok':True,'links_kept':True,'seo_kept':True}),flush=True)
updated=source.replace(before_anchor,top+before_anchor,1).replace(after_anchor,'      </ul>\n'+tail+'    {/if}\n\n    {* PAGES *}',1)
if updated==source or updated.count(marker)!=1 or updated.count('{literal}')!=1:
 raise RuntimeError('Generated template invalid')
backupdir=root.parent/'storefront-backups'/('nav87-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
backupdir.mkdir(mode=0o700,parents=True)
backup=backupdir/'header.mobile.html'
shutil.copy2(str(file),str(backup));os.chmod(str(backup),0o600)
if backup.read_text('utf8')!=source:raise RuntimeError('Backup mismatch')
print('NAV87_BACKUP='+json.dumps({'path':str(backup)}),flush=True)
written=False
try:
 stat=file.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf8',dir=str(file.parent),prefix='.nav87-',delete=False) as tmp:
  tmp.write(updated);name=tmp.name
 os.chmod(name,stat.st_mode & 0o777);os.chown(name,stat.st_uid,stat.st_gid)
 if file.read_text('utf8')!=source:raise RuntimeError('Concurrent file edit')
 os.rename(name,str(file));written=True
 for i,route in enumerate(routes):
  after=http(route);old=baseline[i]
  same=after['http']=='200' and after['rc']==0 and after['title']==old['title'] and after['robots']==old['robots'] and after['canonical']==old['canonical'] and after['buy']==old['buy'] and after['trust']==old['trust']
  links=after['mobile_roots']==old['mobile_roots'] and after['mobile_subs']==old['mobile_subs'] and after['old_category_urls']==old['old_category_urls']
  passed=bool(same and links and after['summary'] and after['quick_count']==6)
  print('NAV87_TEST='+json.dumps({'case':i+1,'page':route,'ok':passed,'http':after['http'],'quick':after['quick_count'],'root_count':after['mobile_roots'],'submenu_count':after['mobile_subs']}),flush=True)
  if not passed:raise RuntimeError('Postdeployment check failed '+route)
 if file.read_text('utf8')!=updated:raise RuntimeError('Template changed during testing')
 print('NAV87_DONE='+json.dumps({'ok':True,'checks':len(routes),'full_categories_retained':True,'quick_links':6,'backup':str(backup)}),flush=True)
except Exception as e:
 if written and file.read_text('utf8')==updated:
  shutil.copy2(str(backup),str(file))
  print('NAV87_ROLLBACK='+json.dumps({'done':True,'reason':str(e)[:260]}),flush=True)
 raise
PY
'''
if __name__=='__main__':
 out=execute(REMOTE)
 for line in out.splitlines():
  if line.startswith('NAV87_'):print(line,flush=True)
 if 'NAV87_DONE=' not in out:raise RuntimeError('Navigation deployment could not be verified')
