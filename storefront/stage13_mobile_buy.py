#!/usr/bin/env python3
"""Stage 13: mobile product purchase shortcut without catalog modifications.

Only the active product template is changed; uncategorized products are excluded.
"""
from __future__ import annotations
import json, os, subprocess, tempfile, urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE=r"""set -eu
python3 - <<'PY'
import os,json,pathlib,tempfile,shutil,subprocess,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
f=root/'wa-data/public/shop/themes/pureMegapolis42/product.html'
before=f.read_text(encoding='utf-8')
marker='          {include file="product.cart.html" inline}'
if before.count(marker)!=1 or 'megapolis-mobile-buy' in before:
 raise RuntimeError('Unexpected theme source, no changes')
addition=r'''          {include file="product.cart.html" inline}
          {* Product-only mobile buying shortcut, no catalog changes *}
          {if $wa->domainUrl() == 'https://profikompany.ru' && !empty($product.category_id)}
            <div id="megapolis-mobile-buy" class="megapolis-mobile-buy" role="group" aria-label="Покупка товара">
              <span class="megapolis-mobile-buy__price">{shop_currency_html($product.price)}</span>
              <button type="button" class="megapolis-mobile-buy__button">Купить</button>
            </div>
            {literal}
            <style>
            @media (min-width:768px) {.megapolis-mobile-buy{display:none!important}}
            @media (max-width:767px) {
             .product-page{padding-bottom:72px}
             .megapolis-mobile-buy{position:fixed;bottom:0;left:0;right:0;z-index:5;display:flex;align-items:center;justify-content:space-between;gap:12px;background:#fff;padding:10px 14px calc(10px + env(safe-area-inset-bottom));box-shadow:0 -3px 18px rgba(0,0,0,.13);box-sizing:border-box}
             .megapolis-mobile-buy.is-hidden{display:none}
             .megapolis-mobile-buy__price{font-size:19px;font-weight:700}
             .megapolis-mobile-buy__button{min-width:130px;background:#7b5e45;color:#fff;border:0;border-radius:5px;font-size:16px;font-weight:600;min-height:44px;cursor:pointer}
             .megapolis-mobile-buy__button:disabled{opacity:.45;cursor:not-allowed}
            }
            </style>
            <script>
            (function(){
             function init(){
              var bar=document.getElementById('megapolis-mobile-buy');
              var form=document.getElementById('s-product-form');
              if(!bar||!form)return;
              var button=bar.querySelector('button');
              var label=bar.querySelector('.megapolis-mobile-buy__price');
              var original=form.querySelector('.product-add input[type="submit"]');
              var price=form.querySelector('.product-price');
              if(!original){bar.classList.add('is-hidden');return}
              function sync(){
               button.disabled=original.disabled;
               if(price)label.textContent=price.textContent.trim();
              }
              sync();
              button.addEventListener('click',function(){sync();if(!button.disabled)original.click()});
              if(window.MutationObserver){var mo=new MutationObserver(sync);mo.observe(form,{attributes:true,subtree:true,childList:true,characterData:true})}
              if(window.IntersectionObserver){var io=new IntersectionObserver(function(entries){bar.classList.toggle('is-hidden',entries[0].isIntersecting)});io.observe(original)}
             }
             if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
            })();
            </script>
            {/literal}
          {/if}'''
updated=before.replace(marker,addition,1)
base=root.parent/'storefront-backups'
base.mkdir(mode=0o700,parents=True,exist_ok=True)
bd=base/('stage13-mobile-buy-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
bd.mkdir(mode=0o700)
shutil.copy2(str(f),str(bd/'product.html'))
os.chmod(str(bd/'product.html'),0o600)
written=False
try:
 st=f.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(f.parent),delete=False) as fp:
  fp.write(updated);tmp=fp.name
 os.chown(tmp,st.st_uid,st.st_gid);os.chmod(tmp,st.st_mode)
 os.replace(tmp,str(f));written=True
 checks=[]
 for path,visible in [('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/',True),('/stul-sevyn-bukle-kofeynyy-2-sht/',False)]:
  p=subprocess.Popen(['curl','-k','-sS','-L','--max-time','30','-w','\n%{http_code}','https://profikompany.ru'+path+'?stage13check=1'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  out,_=p.communicate(timeout=37)
  html,status=out.rsplit('\n',1) if '\n' in out else ('','')
  valid='id="megapolis-mobile-buy"' in html
  ok=p.returncode==0 and status=='200' and valid==visible
  checks.append({'page':path,'http':status,'mobile_bar':valid,'ok':ok})
 if not all(c['ok'] for c in checks):raise RuntimeError('Live checks failed '+json.dumps(checks))
 print('STAGE13_APPLY='+json.dumps({'success':True,'backup':str(bd),'checks':checks},ensure_ascii=False))
except Exception as e:
 if written:shutil.copy2(str(bd/'product.html'),str(f))
 print('STAGE13_ROLLBACK='+json.dumps({'reason':str(e),'restored':written},ensure_ascii=False))
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
  with tempfile.TemporaryDirectory(prefix='storefront-stage13-') as td:
    ssh=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
    pub=Path(ssh+'.pub').read_text().strip()
    entry=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
        'POST',json.dumps({'key':pub,'name':'storefront-stage13-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    kid=entry.get('id')
    if not kid:raise RuntimeError('Unable to register temporary SSH key')
    try:
      r=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
            '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
            input=REMOTE,text=True,capture_output=True,timeout=160)
      for line in r.stdout.splitlines():
        if line.startswith('STAGE13_'):print(line,flush=True)
      if r.returncode:
        raise RuntimeError('Storefront UX fix failed '+r.stdout[-1200:]+' '+r.stderr[-500:])
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
        print('temporary_ssh_key_removed=true',flush=True)
      except Exception:
        print('WARNING: temporary SSH cleanup needs review')
if __name__=='__main__':run()
