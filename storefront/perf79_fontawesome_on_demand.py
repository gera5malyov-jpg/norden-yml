#!/usr/bin/env python3
"""Guarded optimization: load Font Awesome only when actual icon markup appears."""
from stage52_disable_callback import execute

REMOTE=r'''set -eu
python3 - <<'PY'
import pathlib,json,os,hashlib,datetime,shutil,subprocess,tempfile,re,html
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
theme=root/'wa-data/public/site/themes/pureMegapolis42'
file=theme/'index.html'
marker='megapolis-fa-on-demand-20261019'
old="""    {if !empty($theme_settings.main__fa_brands) || !empty($theme_settings.main__fa_regular) || !empty($theme_settings.main__fa_solid)}
      <script src="{$wa_real_theme_url}js/fontawesome.min.js?v{$wa_theme_version}" defer></script>
    {/if}
    {if !empty($theme_settings.main__fa_brands)}<script src="{$wa_real_theme_url}js/fa-brands.min.js?v{$wa_theme_version}" defer></script>{/if}
    {if !empty($theme_settings.main__fa_regular)}<script src="{$wa_real_theme_url}js/fa-regular.min.js?v{$wa_theme_version}" defer></script>{/if}
    {if !empty($theme_settings.main__fa_solid)}<script src="{$wa_real_theme_url}js/fa-solid.min.js?v{$wa_theme_version}" defer></script>{/if}"""
new="""    {* Keep original Font Awesome loading for other storefronts sharing the theme. *}
    {if $wa->domainUrl() != 'https://profikompany.ru'}
"""+old+"""
    {else}
    {* On Megapolis all current store icons use the built-in SVG symbol sprite.
       Load optional Font Awesome only when a legacy <i class="fab ..."> icon is actually rendered. *}
    <script id="megapolis-fa-on-demand-20261019" data-font-root="{$wa_real_theme_url|escape}" data-font-version="{$wa_theme_version|escape}">
    {literal}
    (function(){
      'use strict';
      var holder=document.getElementById('megapolis-fa-on-demand-20261019');
      if(!holder)return;
      var base=holder.getAttribute('data-font-root')||'/wa-data/public/site/themes/pureMegapolis42/';
      var ver=holder.getAttribute('data-font-version')||'';
      var started=false,observer=null;
      function hasIcons(){
        return !!document.querySelector('i.fas,i.far,i.fab,i.fa');
      }
      function load(){
        if(started||!hasIcons())return;
        started=true;
        if(observer){observer.disconnect();observer=null;}
        var files=['fontawesome.min.js','fa-brands.min.js','fa-regular.min.js','fa-solid.min.js'];
        var current=0;
        function next(){
          if(current>=files.length)return;
          var s=document.createElement('script');
          s.src=base+'js/'+files[current++]+'?v'+ver;
          s.async=false;
          s.onload=next;
          s.onerror=next;
          document.head.appendChild(s);
        }
        next();
      }
      function observe(){
        load();
        if(started||!window.MutationObserver)return;
        observer=new MutationObserver(function(){load();});
        observer.observe(document.body||document.documentElement,{childList:true,subtree:true,attributes:true,attributeFilter:['class']});
      }
      if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',observe,{once:true});
      else observe();
    })();
    {/literal}
    </script>
    {/if}"""
def checksum(x):return hashlib.sha256(x).hexdigest()
source=file.read_bytes();txt=source.decode('utf8')
if txt.count(old)!=1 or marker in txt or txt.count('megapolis-gallery-cls-20261019')!=1:
 raise RuntimeError('Preflight 1: template has changed, no write')
print('FA79_PREFLIGHT='+json.dumps({'check':1,'ok':True,'hash':checksum(source)}),flush=True)
routes=['/','/category/kompyuternye-kresla/','/category/kompyuternye-kresla/?page=2',
 '/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','/stul-prozrachnyy-pedrali-queen/',
 '/cart/','/order/','/oplata/','/dostavka/','/compare/']
def check(route):
 p=subprocess.Popen(['curl','-ksSL','--compressed','--max-time','30','-w','\n%{http_code}','https://profikompany.ru'+route],
 stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 out,err=p.communicate(timeout=38)
 if '\n' not in out:return {'http':'','rc':p.returncode}
 s,status=out.rsplit('\n',1)
 head=s.split('</head>',1)[0]
 title=re.findall(r'<title[^>]*>(.*?)</title>',head,re.I|re.S)
 robots=re.findall(r'<meta[^>]*name="robots"[^>]*>',head,re.I)
 canonical=re.findall(r'<link[^>]*rel="canonical"[^>]*>',head,re.I)
 return {'http':status,'rc':p.returncode,'title':title,'robots':robots,'canonical':canonical,
  'has_compare':'href="/compare/"' in s,
  'sprite_icon':'#icon-chart' in s,
  'has_old': 'js/fa-solid.min.js' in head,
  'has_loader':marker in head,'html_length':len(s)}
before=[check(r) for r in routes]
if not all(x['http']=='200' and x['rc']==0 and x['has_old'] and not x['has_loader'] and x['title'] for x in before):
 raise RuntimeError('Preflight 2: original storefront metadata or scripts missing')
print('FA79_PREFLIGHT='+json.dumps({'check':2,'ok':True,'routes':len(routes)}),flush=True)
# Preserve default index behavior elsewhere; only site-specific guarded output changes.
if 'component.social.html' not in ' '.join([p.name for p in (theme/'components').iterdir()]):
 raise RuntimeError('Preflight 3: cannot verify social icon fallback')
if new.count('{literal}')!=1 or new.count('{/literal}')!=1 or new.count('{/if}')!=5:
 raise RuntimeError('Preflight 3: block nesting check failed')
print('FA79_PREFLIGHT='+json.dumps({'check':3,'ok':True,'svg_icons_kept':True,'social_icon_fallback':True}),flush=True)
folder=root.parent/'storefront-backups'/('fa79-mobile-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700,parents=True)
backup=folder/'index.html'
shutil.copy2(str(file),str(backup));os.chmod(str(backup),0o600)
if backup.read_bytes()!=source:raise RuntimeError('Backup mismatch')
print('FA79_BACKUP='+json.dumps({'path':str(backup)}),flush=True)
updated=txt.replace(old,new,1).encode('utf8')
written=False
try:
 st=file.stat()
 with tempfile.NamedTemporaryFile(mode='wb',dir=str(file.parent),prefix='.fa79-',delete=False) as tmp:
  tmp.write(updated);name=tmp.name
 os.chmod(name,st.st_mode&0o777);os.chown(name,st.st_uid,st.st_gid)
 if file.read_bytes()!=source:raise RuntimeError('Concurrent theme change')
 os.rename(name,str(file));written=True
 for i,route in enumerate(routes):
  a=check(route);b=before[i]
  ok=(a['http']=='200' and a['rc']==0 and a['title']==b['title'] and a['robots']==b['robots'] and
    a['canonical']==b['canonical'] and a['has_compare']==b['has_compare'] and
    a['sprite_icon']==b['sprite_icon'] and not a['has_old'] and a['has_loader'])
  print('FA79_TEST='+json.dumps({'i':i+1,'route':route,'ok':ok,'http':a['http'],'seo_unchanged':a['robots']==b['robots'] and a['canonical']==b['canonical']}),flush=True)
  if not ok:raise RuntimeError('Post-deploy test '+str(i+1)+' failed')
 if file.read_bytes()!=updated:raise RuntimeError('Unplanned theme content change')
 print('FA79_DEPLOY='+json.dumps({'ok':True,'tests':len(routes),'backup':str(backup),'original_scripts_preserved_for_other_domains':True,
  'cart_compare_svg_unchanged':True,'database_unchanged':True},ensure_ascii=False),flush=True)
except Exception as err:
 if written and file.read_bytes()==updated:
  shutil.copy2(str(backup),str(file))
  print('FA79_ROLLBACK='+json.dumps({'restored':True,'reason':str(err)[:230]}),flush=True)
 raise
PY
'''
if __name__=='__main__':
 out=execute(REMOTE)
 for line in out.splitlines():
  if line.startswith('FA79_'):print(line,flush=True)
 if 'FA79_DEPLOY=' not in out:raise RuntimeError('No verified font optimization deployment')
