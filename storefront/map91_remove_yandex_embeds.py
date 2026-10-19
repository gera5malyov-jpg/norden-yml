#!/usr/bin/env python3
"""Remove all embedded Yandex maps from profikompany.ru contact blocks.

Scoped Smarty template patch: keeps addresses/phones/mail, does not change
YML, products, orders, prices, inventories or site DB. Three preflight
checks, atomic backup/deploy, live regression checks and rollback.
"""
from stage52_disable_callback import execute

REMOTE=r'''set -eu
python3 - <<'PY'
import os,re,json,hashlib,datetime,pathlib,shutil,subprocess,tempfile
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
file=root/'wa-data/public/site/themes/pureMegapolis42/layouts/layout.contact.html'
domain='https://profikompany.ru'
marker='megapolis-no-embedded-map-20261019'
data=file.read_bytes()
source=data.decode('utf8')
old_shop="{if !empty($theme_settings.company__map)}"
old_info='<div class="col-md-6 col-12 content__info">'
old_map='<div class="col-md-6 col-12 contact__map">'
old_map_end="""              {if !empty($_contact_map)}{$_contact_map}{/if}
            {/if}
          </div>"""
new_map_end=old_map_end+"\n          {/if}"
tablet=re.compile(r"\{\* megapolis-contact-map-responsive-2026:.*?\{/if\}\s*",re.S)
mobile=re.compile(r"\{\* Prevent the Yandex map's fixed 500px iframe.*?\{/if\}\s*",re.S)
def checksum(raw):return hashlib.sha256(raw).hexdigest()
def emit(what,payload):print('MAP91_'+what+'='+json.dumps(payload,ensure_ascii=False),flush=True)
def fetch(path):
 p=subprocess.Popen(['curl','-ksSL','--compressed','-H','Cache-Control: no-cache','--max-time','30','-w','\n%{http_code}',domain+path],
   stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 out,err=p.communicate(timeout=38)
 if '\n' not in out:return {'status':'','rc':p.returncode}
 body,status=out.rsplit('\n',1)
 head=body.split('</head>',1)[0]
 return {'status':status,'rc':p.returncode,
  'title':re.findall(r'<title[^>]*>(.*?)</title>',head,re.I|re.S),
  'description':re.findall(r'<meta\b[^>]*name=["\']description["\'][^>]*>',head,re.I),
  'robots':re.findall(r'<meta\b[^>]*name=["\']robots["\'][^>]*>',head,re.I),
  'canonical':re.findall(r'<link\b[^>]*rel=["\']canonical["\'][^>]*>',head,re.I),
  'yandex_constructor':body.count('api-maps.yandex.ru/services/constructor'),
  'google_embedded':body.count('google.com/maps/embed'),
  'map_div':len(re.findall(r'<div\b[^>]*class=["\'][^"\']*\bcontact__map\b',body,re.I)),
  'contact_section':'contact contact--map' in body,
  'contact_heading':'Для звонков, гостей и писем' in body,
  'contact_column_full':'class="col-12 content__info"' in body,
  'label_removed_style':marker in body,
  'phones_spb':'tel:+7(812)2449264' in body,
  'phones_msk':'tel:+7(499)6776332' in body,
  'email':'mailto:shop@office-mag.com' in body,
  'legal_address':'7-я Красноармейская, д. 25' in body,
  'buy_form':'id="s-product-form"' in body,
  'category_links':'href="/category/kompyuternye-kresla/"' in body,
  'mobile_menu':'class="megapolis-menu-all"' in body}
if not all([source.count(old_shop)==1,source.count(old_info)==1,source.count(old_map)==1,
            source.count(old_map_end)==1,marker not in source,
            len(tablet.findall(source))==1,len(mobile.findall(source))==1]):
 raise RuntimeError('PREFLIGHT 1: contact template markers changed, no file changed')
emit('PREFLIGHT',{'check':1,'ok':True,'source_sha':checksum(data),'template':str(file.relative_to(root))})
routes=['/','/?utm_source=mapremovaltest','/o-kompanii/','/category/kompyuternye-kresla/',
 '/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/',
 '/stul-prozrachnyy-pedrali-queen/','/oplata/','/dostavka/','/garantiya/',
 '/compare/1186404,1186400/','/search/?query=stul','/order/']
base=[fetch(path) for path in routes]
if not all(x['status']=='200' and x['rc']==0 and len(x['title'])==1 for x in base):
 raise RuntimeError('PREFLIGHT 2: baseline pages unhealthy or unexpected')
if not (base[0]['yandex_constructor']==1 and base[0]['map_div']==1 and
        base[0]['contact_section'] and base[0]['contact_heading'] and
        all(base[0][key] for key in ('phones_spb','phones_msk','email','legal_address'))):
 raise RuntimeError('PREFLIGHT 2: homepage map or contact details changed')
emit('PREFLIGHT',{'check':2,'ok':True,'baseline_pages':len(routes),
 'home_map_scripts':base[0]['yandex_constructor'],'contact_details_present':True})
if not all('noindex,follow' in ''.join(base[i]['robots']).lower() for i in (9,10)):
 raise RuntimeError('PREFLIGHT 3: prior comparison/search SEO status missing')
if not base[4]['buy_form'] or not base[0]['mobile_menu']:
 raise RuntimeError('PREFLIGHT 3: catalog purchase or mobile navigation guard absent')
emit('PREFLIGHT',{'check':3,'ok':True,'seo_guard':True,'cart_and_menu_untouched':True})
# Separate output from source, remove old, now pointless map-only CSS.
updated=source.replace(old_shop,
 "{if !empty($theme_settings.company__map) && $wa->domainUrl() != 'https://profikompany.ru'}",1)
updated=updated.replace(old_info,
 '<div class="{if $wa->domainUrl() == \'https://profikompany.ru\'}col-12{else}col-md-6 col-12{/if} content__info">',1)
updated=updated.replace(old_map,
 "{if $wa->domainUrl() != 'https://profikompany.ru'}\n          "+old_map,1)
updated=updated.replace(old_map_end,new_map_end,1)
updated,n1=tablet.subn('',updated,1)
updated,n2=mobile.subn('',updated,1)
new_css="""{* megapolis-no-embedded-map-20261019: legal contact information without third-party map and ads. *}
{if $wa->domainUrl() == 'https://profikompany.ru'}
{literal}
<style id="megapolis-no-embedded-map-20261019">
  .contact--map .content__info {box-sizing:border-box;max-width:1080px;margin-right:auto;margin-left:auto;padding:34px 36px}
  @media(max-width:767px){.contact--map .content__info{padding:24px 20px}}
</style>
{/literal}
{/if}
"""
updated=updated.rstrip()+"\n\n"+new_css
if not (n1==1 and n2==1 and updated.count(marker)==2 and
        updated.count('{literal}')==source.count('{literal}') and
        updated.count('$_contact_map')==source.count('$_contact_map')):
 raise RuntimeError('Generated map-removal template failed validation')
folder=root.parent/'storefront-backups'/('map91-no-yandex-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700,parents=True)
backup=folder/'layout.contact.html'
shutil.copy2(str(file),str(backup));os.chmod(str(backup),0o600)
if checksum(backup.read_bytes())!=checksum(data):raise RuntimeError('Backup integrity check failed')
emit('BACKUP',{'file':str(backup),'sha256':checksum(data)})
written=False
newbytes=updated.encode('utf8')
try:
 stat=file.stat()
 with tempfile.NamedTemporaryFile('wb',dir=str(file.parent),prefix='.map91-',delete=False) as temp:
  temp.write(newbytes);tmp=temp.name
 os.chmod(tmp,stat.st_mode&0o777);os.chown(tmp,stat.st_uid,stat.st_gid)
 if checksum(file.read_bytes())!=checksum(data):
  raise RuntimeError('Concurrent template edit, refusing unsafe replace')
 os.replace(tmp,str(file));written=True
 for i,path in enumerate(routes):
  row=fetch(path);old=base[i]
  safe=(row['status']=='200' and row['rc']==0 and row['title']==old['title'] and
        row['description']==old['description'] and row['robots']==old['robots'] and
        row['canonical']==old['canonical'] and row['buy_form']==old['buy_form'] and
        row['mobile_menu']==old['mobile_menu'] and row['category_links']==old['category_links'])
  map_gone=(row['yandex_constructor']==0 and row['google_embedded']==0 and row['map_div']==0)
  if i in (0,1):
   contact=(row['contact_section'] and row['contact_heading'] and row['contact_column_full'] and
      row['label_removed_style'] and all(row[k] for k in ('phones_spb','phones_msk','email','legal_address')))
  else:
   contact=(row['contact_section']==old['contact_section'] and row['legal_address']==old['legal_address'])
  ok=bool(safe and map_gone and contact)
  emit('TEST',{'case':i+1,'url':path,'ok':ok,'http':row['status'],
    'map_script_count':row['yandex_constructor'],'map_divs':row['map_div'],
    'company_info_kept':contact,'metadata_unchanged':safe})
  if not ok:raise RuntimeError('Live verification failed: '+path)
 if checksum(file.read_bytes())!=checksum(newbytes):raise RuntimeError('Template modified during checks')
 emit('DEPLOY',{'ok':True,'tests':len(routes),'backup':str(backup),
   'removed_yandex_constructor':True,'contact_address_phones_email_kept':True,
   'category_product_yml_db_unchanged':True,'map_loaded_on_homepage':False,
   'other_domains_original_map_markup_kept':True})
except Exception as err:
 if written and file.read_bytes()==newbytes:
  shutil.copy2(str(backup),str(file))
  emit('ROLLBACK',{'restored':True,'reason':str(err)[:270]})
 raise
PY
'''
if __name__=='__main__':
 result=execute(REMOTE)
 for line in result.splitlines():
  if line.startswith('MAP91_'):print(line,flush=True)
 if 'MAP91_DEPLOY=' not in result:raise RuntimeError('Map removal not verified')
