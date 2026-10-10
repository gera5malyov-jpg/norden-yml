#!/usr/bin/env python3
"""Stage 11: put existing shipping and payment information beside purchase CTA.

Only the active Webasyst product template is modified. Uncategorised products
are intentionally excluded; product records and all commerce integrations are untouched.
"""
from __future__ import annotations
import json, os, subprocess, tempfile, urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE=r"""set -eu
python3 - <<'PY'
import json,os,pathlib,shutil,subprocess,tempfile,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
f=root/'wa-data/public/shop/themes/pureMegapolis42/product.html'
source=f.read_text(encoding='utf-8')
needle='''          {include file="product.cart.html" inline}'''
snippet='''          {include file="product.cart.html" inline}
          {* Purchase help: only products assigned to an existing category *}
          {if $wa->domainUrl() == 'https://profikompany.ru' && !empty($product.category_id)}
            <p class="product-page__buy-help" style="margin:12px 0; font-size:0.9em">
              <a href="/dostavka/">Условия доставки</a>
              <span aria-hidden="true"> · </span>
              <a href="/oplata/">Способы оплаты</a>
            </p>
          {/if}'''
if source.count(needle)!=1:
    raise RuntimeError('Product purchase include differs from audited version')
if 'product-page__buy-help' in source:
    raise RuntimeError('Purchase-help markup already present')
updated=source.replace(needle,snippet,1)
backups=root.parent/'storefront-backups'
backups.mkdir(mode=0o700,parents=True,exist_ok=True)
directory=backups/('stage11-purchase-help-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
directory.mkdir(mode=0o700)
backup=directory/'product.html'
shutil.copy2(str(f),str(backup))
os.chmod(str(backup),0o600)
written=False
try:
    st=f.stat()
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(f.parent),prefix='.stage11-cta-',delete=False) as out:
        out.write(updated)
        tmp=out.name
    os.chown(tmp,st.st_uid,st.st_gid)
    os.chmod(tmp,st.st_mode)
    os.replace(tmp,str(f))
    written=True
    testcases=[
      ('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/',True),
      ('/stul-sevyn-bukle-kofeynyy-2-sht/',False)
    ]
    checks=[]
    for path,expect_help in testcases:
      url='https://profikompany.ru'+path+'?stage11_check='+str(int(time.time()))
      args=['curl','-k','-sS','-L','--max-time','35','-w','\n%{http_code}',url]
      p=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
      content,err=p.communicate(timeout=42)
      page,status=content.rsplit('\n',1) if '\n' in content else ('','')
      product_markup=page.split('class="product-page"')[1].split('</article>')[0] if 'class="product-page"' in page else ''
      count=product_markup.count('class="product-page__buy-help"')
      present=count==1 and 'href="/dostavka/"' in product_markup and 'href="/oplata/"' in product_markup
      ok=p.returncode==0 and status=='200' and present==expect_help
      checks.append({'path':path,'http':status,'help_visible':present,
         'expected':expect_help,'ok':ok})
    if not all(c['ok'] for c in checks):
        raise RuntimeError('Live product UX checks failed '+json.dumps(checks,ensure_ascii=False))
    print('STAGE11_APPLY='+json.dumps({'status':'success','backup':str(backup),
      'file':str(f.relative_to(root)),'checks':checks},ensure_ascii=False))
except Exception as exc:
    if written:shutil.copy2(str(backup),str(f))
    print('STAGE11_ROLLBACK='+json.dumps({'reason':str(exc),'restored':written},ensure_ascii=False))
    raise
PY
"""

def run():
  key=os.environ.get('NETANGELS_API_KEY','').strip()
  if not key:raise RuntimeError('Missing NETANGELS_API_KEY secret')
  token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
      urllib.parse.urlencode({'api_key':key}).encode(),
      {'Content-Type':'application/x-www-form-urlencoded'}).get('token')
  if not token:raise RuntimeError('NetAngels gateway token missing')
  headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
  with tempfile.TemporaryDirectory(prefix='storefront-stage11-') as td:
    ssh=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
    pub=Path(ssh+'.pub').read_text().strip()
    entry=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
        'POST',json.dumps({'key':pub,'name':'storefront-stage11-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    kid=entry.get('id')
    if not kid:raise RuntimeError('Unable to register temporary SSH key')
    try:
      r=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
            '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
            input=REMOTE,text=True,capture_output=True,timeout=160)
      for line in r.stdout.splitlines():
        if line.startswith('STAGE11_'):print(line,flush=True)
      if r.returncode:
        raise RuntimeError('Storefront UX fix failed '+r.stdout[-1200:]+' '+r.stderr[-500:])
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
        print('temporary_ssh_key_removed=true',flush=True)
      except Exception:
        print('WARNING: temporary SSH cleanup needs review')
if __name__=='__main__':run()
