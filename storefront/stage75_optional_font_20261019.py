#!/usr/bin/env python3
"""Stage75: safe versioned Montserrat font-display optional; layout stability on slow connections."""
from stage52_disable_callback import execute
REMOTE = r'''set -eu
python3 - <<'PY'
import os,hashlib,datetime,pathlib,shutil,tempfile,json,subprocess,re
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
theme=root/'wa-data/public/site/themes/pureMegapolis42'
index=theme/'index.html'
fonts=theme/'css/fonts'
oldcss=fonts/'montserrat.megapolis53.css'
newcss=fonts/'montserrat.megapolis75.css'
oldlink='montserrat.megapolis53.css?v=20261012'
newlink='montserrat.megapolis75.css?v=20261019'
routes=[
 '/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/',
 '/stul-prozrachnyy-pedrali-queen/',
 '/','/category/kompyuternye-kresla/','/category/kompyuternye-kresla/?page=2',
 '/oplata/','/dostavka/','/o-kompanii/','/search/?query=test','/order/']
def sha(b):return hashlib.sha256(b).hexdigest()
def emit(k,d):print('STAGE75_'+k+'='+json.dumps(d,ensure_ascii=False),flush=True)
def fetch(u):
 proc=subprocess.Popen(['curl','-ksSL','--compressed','--max-time','33','-w','\n%{http_code}',
  'https://profikompany.ru'+u],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 page,error=proc.communicate(timeout=40)
 if '\n' in page:body,status=page.rsplit('\n',1)
 else:body,status='',''
 head=body.split('</head>',1)[0]
 return {'http':status,'rc':proc.returncode,'title':re.findall(r'<title[^>]*>(.*?)</title>',head,re.I|re.S),
  'canonical':re.findall(r'<link[^>]*rel="canonical"[^>]*>',head,re.I),
  'description':re.findall(r'<meta[^>]*name="description"[^>]*>',head,re.I),
  'robots':re.findall(r'<meta[^>]*name="robots"[^>]*>',head,re.I),
  'has_font_new':newlink in head,'has_font_old':oldlink in head,
  'phones':'2449264' in body,'product_buy':'megapolis-mobile-buy__button' in body,
  'has_product_gallery':('product-gallery--extimg' in body or 'product-gallery__main' in body)}
orig=index.read_bytes()
txt=orig.decode('utf8')
font_source=oldcss.read_bytes()
font_text=font_source.decode('utf8')
emit('DIAG',{'old_link_count':txt.count(oldlink),'new_link_count':txt.count(newlink),'newcss_exists':newcss.exists(),
 'oldcss_exists':oldcss.is_file(),'contexts':[txt[max(0,m.start()-135):m.start()+160] for m in list(re.finditer(re.escape(oldlink),txt))[:2]]})
if txt.count(oldlink)!=2 or newlink in txt or newcss.exists() or not oldcss.is_file():
 raise RuntimeError('PREFLIGHT 1: old font link or source differs, abort')
matches=list(re.finditer(re.escape(oldlink),txt))
if not all("if $wa->domainUrl() == 'https://profikompany.ru' && $theme_settings.main__font == \\"montserrat\\"" in txt[max(0,m.start()-190):m.start()] for m in matches):
 raise RuntimeError('Existing font references are not scoped to the expected site')
if font_text.count('@font-face')!=18 or font_text.count('font-display:swap')!=18 or 'font-display:optional' in font_text:
 raise RuntimeError('PREFLIGHT 1: unexpected Montserrat font-face contents')
updated_font=font_text.replace('font-display:swap','font-display:optional').encode('utf8')
updated_html=txt.replace(oldlink,newlink,2).encode('utf8')
if font_text.count('montserrat-megapolis53-')!=18:
 raise RuntimeError('PREFLIGHT 1: font files mismatch')
emit('PREFLIGHT',{'check':1,'ok':True,'face_count':18,'index_sha':sha(orig),
 'style_sha':sha(font_source),'styles_preserved':True})
baseline=[]
for i,path in enumerate(routes):
 x=fetch(path)
 if x['rc'] or x['http']!='200' or not x['title'] or not x['phones'] or not x['has_font_old'] or x['has_font_new']:
  raise RuntimeError('PREFLIGHT 2: invalid live page '+path)
 baseline.append(x)
emit('PREFLIGHT',{'check':2,'ok':True,'routes':len(routes)})
if not baseline[0]['product_buy'] or not baseline[0]['has_product_gallery'] or 'noindex' not in ','.join(baseline[8]['robots']).lower():
 raise RuntimeError('PREFLIGHT 3: important store functionality differs')
for i in (1,2,3,4,5,6,7,9):
 if baseline[i]['product_buy'] and i>1:raise RuntimeError('PREFLIGHT 3: unexpected mobile buy marker')
emit('PREFLIGHT',{'check':3,'ok':True,'buy_button_preserved':True,'search_noindex_preserved':True})
folder=root.parent/'storefront-backups'/('stage75-font-optional-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700,parents=True)
bkindex=folder/'index.html';bkcss=folder/'montserrat.megapolis53.css'
shutil.copy2(str(index),str(bkindex));shutil.copy2(str(oldcss),str(bkcss))
os.chmod(str(bkindex),0o600);os.chmod(str(bkcss),0o600)
if bkindex.read_bytes()!=orig or bkcss.read_bytes()!=font_source:
 raise RuntimeError('Backup checksum differs')
emit('BACKUP',{'directory':str(folder),'index_sha':sha(orig),'font_sha':sha(font_source)})
created=False;written=False
try:
 stat=oldcss.stat()
 with tempfile.NamedTemporaryFile('wb',dir=str(fonts),prefix='.stage75-font-',delete=False) as fh:
  fh.write(updated_font);tmp=fh.name
 os.chmod(tmp,0o644);os.chown(tmp,stat.st_uid,stat.st_gid)
 if newcss.exists():raise RuntimeError('Concurrent new asset created')
 os.rename(tmp,str(newcss));created=True
 indexstat=index.stat()
 with tempfile.NamedTemporaryFile('wb',dir=str(index.parent),prefix='.stage75-index-',delete=False) as fh:
  fh.write(updated_html);tmp=fh.name
 os.chmod(tmp,indexstat.st_mode&0o777);os.chown(tmp,indexstat.st_uid,indexstat.st_gid)
 if index.read_bytes()!=orig:raise RuntimeError('Index concurrently changed')
 os.rename(tmp,str(index));written=True
 os.utime(str(index),None)
 for i,path in enumerate(routes):
  x=fetch(path);b=baseline[i]
  ok=(x['rc']==0 and x['http']=='200' and x['title']==b['title']
      and x['description']==b['description'] and x['robots']==b['robots']
      and x['canonical']==b['canonical'] and x['phones']==b['phones']
      and x['product_buy']==b['product_buy'] and x['has_product_gallery']==b['has_product_gallery']
      and x['has_font_new'] and not x['has_font_old'])
  emit('TEST',{'number':i+1,'url':path,'http':x['http'],'ok':bool(ok),
   'metadata_unchanged':x['title']==b['title'] and x['canonical']==b['canonical']})
  if not ok:raise RuntimeError('Acceptance test %d failed (%s)'%(i+1,path))
 p=subprocess.Popen(['curl','-ksSL','--compressed','--max-time','20','-w','\n%{http_code}',
    'https://profikompany.ru/wa-data/public/site/themes/pureMegapolis42/css/fonts/montserrat.megapolis75.css'],
    stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 response,error=p.communicate(timeout=27)
 content,status=response.rsplit('\n',1)
 if p.returncode or status!='200' or content.count('font-display:optional')!=18 or len(content.encode('utf8'))!=len(updated_font):
  raise RuntimeError('New stylesheet delivery or content invalid')
 if index.read_bytes()!=updated_html or newcss.read_bytes()!=updated_font or oldcss.read_bytes()!=font_source:
  raise RuntimeError('Source checksum mismatch after deployment')
 emit('DEPLOY',{'success':True,'checks':len(routes),'font_faces':18,'backup':str(folder),
  'new_css':'montserrat.megapolis75.css','site_template_only':True,'catalog_or_pricing_changed':False,
  'original_css_unchanged':True})
except Exception as error:
 if written and index.read_bytes()==updated_html:
  shutil.copy2(str(bkindex),str(index));os.utime(str(index),None)
  emit('ROLLBACK',{'html_restored':True})
 if created and newcss.exists() and newcss.read_bytes()==updated_font:
  newcss.unlink();emit('ROLLBACK',{'new_asset_removed':True})
 emit('ERROR',{'reason':str(error)[:330]})
 raise
PY
'''
if __name__=='__main__':
 response=execute(REMOTE)
 for line in response.splitlines():
  if line.startswith('STAGE75_'):print(line,flush=True)
 if 'STAGE75_DEPLOY=' not in response:
  raise RuntimeError('Stage75 was not fully deployed')
