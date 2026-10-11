#!/usr/bin/env python3
"""Add verified buyer reassurance information and fix product SEO snippet punctuation.

No data modifications. Two theme backups with rollback.
Copies active Smarty template to private backup, rolls back if smoke fails.
"""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
python3 - <<'PY'
import json,os,pathlib,shutil,subprocess,tempfile,time,re
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
site=root/'wa-data/public/site/themes/pureMegapolis42/index.html'
product=root/'wa-data/public/shop/themes/pureMegapolis42/product.html'
old_head=site.read_text(encoding='utf-8')
old_product=product.read_text(encoding='utf-8')
meta_anchor='''      {else}
        <meta name="description" content="{$wa->meta('description')|escape}" />
      {/if}
    {/if}'''
meta_replacement='''      {elseif $wa->domainUrl() == 'https://profikompany.ru' && $wa->param('action') == 'product'}
        <meta name="description" content="{$wa->meta('description')|replace:'руб..':'руб.'|escape}" />
      {else}
        <meta name="description" content="{$wa->meta('description')|escape}" />
      {/if}
    {/if}'''
product_anchor='''          {include file="product.cart.html" inline}'''
trust='''          {include file="product.cart.html" inline}
          {* Scope buyer information to categorized products only, with truthful service terms. *}
          {if $wa->domainUrl() == 'https://profikompany.ru' && !empty($product.category_id)}
            <section class="megapolis-purchase-trust" aria-label="Информация перед покупкой">
              <div class="megapolis-purchase-trust__title">Перед оформлением заказа</div>
              <p>Доставляем мебель по Санкт-Петербургу и России. Стоимость и условия доставки уточняются при оформлении заказа.</p>
              <nav class="megapolis-purchase-trust__links" aria-label="Условия заказа">
                <a href="/dostavka/">Доставка</a>
                <a href="/oplata/">Способы оплаты</a>
                <a href="/garantiya/">Гарантия и возврат</a>
              </nav>
              <p class="megapolis-purchase-trust__contact">Поможем уточнить детали: <a href="tel:+78122449264">+7 (812) 244-92-64</a></p>
            </section>
            {literal}
              <style>
                .megapolis-purchase-trust{padding:15px 17px;margin:12px 0 20px;background:#f7f7f6;border:1px solid #e5e3e0;border-radius:8px;color:#353535;font-size:14px;line-height:1.55}
                .megapolis-purchase-trust__title{font-size:16px;line-height:1.35;font-weight:700;margin-bottom:5px}
                .megapolis-purchase-trust p{margin:0 0 9px}
                .megapolis-purchase-trust__links{display:flex;gap:7px 18px;flex-wrap:wrap;margin:5px 0 10px}
                .megapolis-purchase-trust a{color:#694932;text-decoration:underline;text-underline-offset:2px}
                .megapolis-purchase-trust a:focus-visible{outline:2px solid #694932;outline-offset:2px}
                .megapolis-purchase-trust__contact{margin-bottom:0!important}
              </style>
            {/literal}
          {/if}'''
if old_head.count(meta_anchor)!=1 or old_product.count(product_anchor)!=1 or 'megapolis-purchase-trust' in old_product:
 raise RuntimeError('Live theme differs from audited source; abort before writes')
changed={site:old_head.replace(meta_anchor,meta_replacement,1),product:old_product.replace(product_anchor,trust,1)}
backupdir=root.parent/'storefront-backups'/('stage37-buyer-trust-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
backupdir.mkdir(mode=0o700,parents=True,exist_ok=True)
for path in changed:
 dest=backupdir/path.name
 shutil.copy2(str(path),str(dest));os.chmod(str(dest),0o600)
written=[]
try:
 for path,content in changed.items():
  st=path.stat()
  with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(path.parent),prefix='.stage37-',delete=False) as f:
   f.write(content);tmp=f.name
  os.chmod(tmp,st.st_mode&0o777);os.chown(tmp,st.st_uid,st.st_gid)
  os.replace(tmp,str(path));written.append(path)
 checks=[]
 for route,expect_trust,expect_price_fix in [
    ('/',False,False),
    ('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/',True,True),
    ('/stul-sevyn-bukle-kofeynyy-2-sht/',False,True),
    ('/category/kompyuternye-kresla/',False,False),
    ('/order/',False,False)]:
  cmd=['curl','-ksSL','--max-time','32','-w','\n%{http_code}',
      'https://profikompany.ru'+route+'?stage37_verify='+str(int(time.time()))]
  p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  out,err=p.communicate(timeout=39)
  page,status=out.rsplit('\n',1) if '\n' in out else ('','')
  head=page.split('</head>',1)[0]
  desc=re.search('<meta name="description" content="([^"]+)"',head)
  descval=desc.group(1) if desc else ''
  present='class="megapolis-purchase-trust"' in page
  links=all(x in page for x in ('href="/dostavka/"','href="/oplata/"','href="/garantiya/"'))
  ok=(p.returncode==0 and status=='200' and present==expect_trust
    and (not expect_trust or links)
    and (not expect_price_fix or ('руб..' not in descval and 'руб.' in descval))
    and (not expect_trust or 'id="megapolis-mobile-buy"' in page))
  checks.append({'route':route,'http':status,'trust':present,'price_meta_fixed':expect_price_fix and 'руб..' not in descval,'ok':ok})
 if not all(x['ok'] for x in checks):
  raise RuntimeError('Site smoke test failed '+json.dumps(checks,ensure_ascii=False))
 print('STAGE37_APPLY='+json.dumps({'ok':True,'backup':str(backupdir),'checks':checks},ensure_ascii=False))
except Exception as ex:
 for path in reversed(written):shutil.copy2(str(backupdir/path.name),str(path))
 print('STAGE37_ROLLBACK='+json.dumps({'reason':str(ex),'restored':[str(x) for x in written]},ensure_ascii=False))
 raise
PY
"""

def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('Missing NETANGELS_API_KEY secret')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
   urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels gateway token missing')
 hdr={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage37-buyer-') as td:
  ssh=td+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
  pub=Path(ssh+'.pub').read_text().strip()
  row=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'storefront-stage37-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),hdr)
  kid=row.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
     '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
     input=REMOTE,text=True,capture_output=True,timeout=240)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE37_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('SEO update failed '+p.stdout[-1300:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,hdr)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH key cleanup needs review')
if __name__=='__main__':run()
