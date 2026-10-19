#!/usr/bin/env python3
"""Enhance existing product trust details without fabricated testimonials or return claims."""
from stage52_disable_callback import execute
REMOTE=r'''set -eu
python3 - <<'PY'
import os,re,json,pathlib,shutil,datetime,subprocess,tempfile,hashlib
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
file=root/'wa-data/public/shop/themes/pureMegapolis42/product.html'
source=file.read_text('utf8')
marker='megapolis-verified-trust-20261019'
old='''              <div class="megapolis-purchase-trust__title">Перед оформлением заказа</div>
              <p>Товары отправляются из разных городов. Доставка рассчитывается и согласуется отдельно: стоимость зависит от города отправки, адреса получения, веса и объёма заказа.</p>
              <nav class="megapolis-purchase-trust__links" aria-label="Условия заказа">
                <a href="/dostavka/">Доставка</a>
                <a href="/oplata/">Способы оплаты</a>
                <a href="/garantiya/">Гарантия и возврат</a>
              </nav>'''
new='''              {* megapolis-verified-trust-20261019: references to existing public terms only, no invented ratings. *}
              <div class="megapolis-purchase-trust__title">Покупка с понятными условиями</div>
              <nav class="megapolis-purchase-trust__facts" aria-label="Информация о продавце и условиях">
                <a href="/oplata/"><strong>Для организаций</strong><span>Счёт и документы к заказу</span></a>
                <a href="/garantiya/"><strong>Гарантия и возврат</strong><span>Условия и порядок обращения</span></a>
                <a href="/dostavka/"><strong>Доставка по России</strong><span>Стоимость согласуем отдельно</span></a>
              </nav>
              <p>Товары могут отправляться из разных городов. Стоимость доставки зависит от адреса, веса и объёма заказа и согласуется отдельно.</p>
              <p class="megapolis-purchase-trust__seller"><a href="/o-kompanii/">Информация о компании «Мегаполис»</a></p>'''
oldcss='''                .megapolis-purchase-trust__links{display:flex;gap:7px 18px;flex-wrap:wrap;margin:5px 0 10px}'''
newcss='''                .megapolis-purchase-trust__facts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin:12px 0 12px}
                .megapolis-purchase-trust__facts a{min-height:70px;display:flex;flex-direction:column;justify-content:center;gap:3px;padding:10px 11px;background:#fff;border:1px solid #e3ded8;border-radius:6px;text-decoration:none;color:#302c28}
                .megapolis-purchase-trust__facts a strong{font-size:13px;color:#2f2821;line-height:1.35}
                .megapolis-purchase-trust__facts a span{font-size:12px;line-height:1.35;color:#5e5954}
                .megapolis-purchase-trust__seller{margin-top:7px!important}
                @media(max-width:800px){.megapolis-purchase-trust__facts{grid-template-columns:1fr}.megapolis-purchase-trust__facts a{min-height:52px}}'''
def check(url):
 p=subprocess.Popen(['curl','-ksSL','--compressed','--max-time','31','-w','\n%{http_code}',
 'https://profikompany.ru'+url],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 out,err=p.communicate(timeout=39)
 html,status=out.rsplit('\n',1) if '\n' in out else ('','')
 head=html.split('</head>',1)[0]
 return {'status':status,'rc':p.returncode,'title':re.findall(r'<title[^>]*>(.*?)</title>',head,re.I|re.S),
 'robots':re.findall(r'<meta[^>]*name=["\']robots["\'][^>]*>',head,re.I),
 'canonical':re.findall(r'<link[^>]*rel=["\']canonical["\'][^>]*>',head,re.I),
 'has_trust':'class="megapolis-purchase-trust"' in html,
 'has_marker':marker in html,'facts':len(re.findall(r'<a href="/(?:oplata|garantiya|dostavka)/"><strong>',html)),
 'has_company_link':'href="/o-kompanii/">Информация о компании' in html,
 'existing_payment':'href="/oplata/"' in html,'existing_guarantee':'href="/garantiya/"' in html,
 'existing_delivery':'href="/dostavka/"' in html,
 'add_to_cart':'id="s-product-form"' in html,
 'availability':'megapolis-orderable-20261019' in html,
 'mobile_menu':'class="megapolis-menu-all"' in html,
 'compare_links':len(re.findall(r'href="/compare/',html))}
if source.count(old)!=1 or source.count(oldcss)!=1 or marker in source:
 raise RuntimeError('Preflight 1 changed trust template')
if source.count('megapolis-purchase-trust__contact')<2 or '{literal}' not in source or 'megapolis-mobile-buy' not in source:
 raise RuntimeError('Preflight 1 existing trust or mobile buy missing')
print('TRUST88_PRE='+json.dumps({'check':1,'ok':True,'hash':hashlib.sha256(source.encode()).hexdigest()}),flush=True)
routes=[
 '/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/',
 '/stul-prozrachnyy-pedrali-queen/',
 '/','/category/kompyuternye-kresla/','/category/kompyuternye-kresla/?page=2',
 '/oplata/','/garantiya/','/dostavka/','/o-kompanii/','/compare/1186404,1186400/',
 '/search/?query=stul','/cart/']
base=[check(u) for u in routes]
if not all(v['status']=='200' and v['rc']==0 and len(v['title'])==1 for v in base):
 raise RuntimeError('Preflight 2 failed storefront')
if not base[0]['has_trust'] or not base[0]['add_to_cart'] or not base[0]['availability']:
 raise RuntimeError('Preflight 2 product buy and existing trust absent')
if not all(base[0][n] for n in ('existing_payment','existing_guarantee','existing_delivery')):
 raise RuntimeError('Preflight 2 existing trust pages unlinked')
print('TRUST88_PRE='+json.dumps({'check':2,'ok':True,'routes':len(routes)}),flush=True)
if not base[0]['mobile_menu'] or not all('noindex' in ''.join(base[i]['robots']).lower() for i in (9,10)):
 raise RuntimeError('Preflight 3 prior SEO/nav changes missing')
if any(base[i]['has_marker'] for i in range(len(base))):
 raise RuntimeError('Preflight 3 duplicate deployment')
print('TRUST88_PRE='+json.dumps({'check':3,'ok':True,'nav_and_seo_preserved':True}),flush=True)
updated=source.replace(old,new,1).replace(oldcss,newcss,1)
if updated==source or updated.count(marker)!=1 or updated.count('{literal}')!=source.count('{literal}'):
 raise RuntimeError('Generated code invalid')
folder=root.parent/'storefront-backups'/('trust88-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(parents=True,mode=0o700)
backup=folder/'product.html';shutil.copy2(str(file),str(backup));os.chmod(str(backup),0o600)
if backup.read_text('utf8')!=source:raise RuntimeError('Backup invalid')
print('TRUST88_BACKUP='+json.dumps({'path':str(backup)}),flush=True)
written=False
try:
 st=file.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf8',dir=str(file.parent),prefix='.trust88-',delete=False) as tmp:
  tmp.write(updated);name=tmp.name
 os.chmod(name,st.st_mode & 0o777);os.chown(name,st.st_uid,st.st_gid)
 if file.read_text('utf8')!=source:raise RuntimeError('Concurrent template edit')
 os.rename(name,str(file));written=True
 for i,u in enumerate(routes):
  after=check(u);before=base[i]
  untouched=after['status']=='200' and after['rc']==0 and after['title']==before['title'] and after['robots']==before['robots'] and after['canonical']==before['canonical'] and after['add_to_cart']==before['add_to_cart'] and after['availability']==before['availability'] and after['mobile_menu']==before['mobile_menu'] and after['compare_links']==before['compare_links']
  if before['has_trust']:
   expected=after['has_marker'] and after['facts']==3 and after['has_company_link']
  else:
   expected=not after['has_marker'] and after['facts']==0
  ok=bool(untouched and expected)
  print('TRUST88_TEST='+json.dumps({'case':i+1,'route':u,'ok':ok,'facts':after['facts'],'status':after['status']}),flush=True)
  if not ok:raise RuntimeError('Trust postcheck failed '+u)
 if file.read_text('utf8')!=updated:raise RuntimeError('Template hash mismatch')
 print('TRUST88_DONE='+json.dumps({'success':True,'tests':len(routes),'backup':str(backup),'no_fake_reviews':True,'no_product_db_changes':True}),flush=True)
except Exception as e:
 if written and file.read_text('utf8')==updated:
  shutil.copy2(str(backup),str(file))
  print('TRUST88_ROLLBACK='+json.dumps({'restored':True,'reason':str(e)[:240]}),flush=True)
 raise
PY
'''
if __name__=='__main__':
 out=execute(REMOTE)
 for line in out.splitlines():
  if line.startswith('TRUST88_'):print(line,flush=True)
 if 'TRUST88_DONE=' not in out:raise RuntimeError('Trust display change not verified')
