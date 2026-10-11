#!/usr/bin/env python3
"""Read-only audit of product buying flow and image conversion tooling."""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
python3 - <<'PY'
import os,json,pathlib,shutil,subprocess,re
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
site=root/'wa-data/public/site/themes/pureMegapolis42'
shop=root/'wa-data/public/shop/themes/pureMegapolis42'
tooling={'magick':shutil.which('magick'),'convert':shutil.which('convert'),'cwebp':shutil.which('cwebp'),'php':shutil.which('php')}
try:
 from PIL import Image
 tooling['pillow']=True
except ImportError: tooling['pillow']=False
print('STAGE35_TOOLING='+json.dumps(tooling))
for rel,markers in [
('wa-data/public/shop/themes/pureMegapolis42/product.html',['product.cart','product-page__buy-help','megapolis-mobile-buy','product-page__']),
('wa-data/public/shop/themes/pureMegapolis42/product.cart.html',['product-add','product-price','product-add-to-cart']),
('wa-data/public/site/themes/pureMegapolis42/layouts/layout.slider.html',['slider__image','slider__picture']),
('wa-data/public/site/themes/pureMegapolis42/layouts/layout.footer.html',['Заказать звонок','Доставка']),
]:
 p=root/rel
 if not p.is_file():continue
 lines=p.read_text(encoding='utf-8',errors='replace').splitlines()
 hits=[]
 for i,line in enumerate(lines):
  if any(m in line for m in markers):
   for j in range(max(0,i-2),min(len(lines),i+4)):
    hits.append((j+1,lines[j][:400]))
 uniq=list(dict.fromkeys(hits))[:95]
 print('STAGE35_SOURCE='+json.dumps({'file':rel,'lines':[{'n':n,'text':t} for n,t in uniq]},ensure_ascii=False))
for i in range(1,8):
 p=site/('img/slider/slider_%02d.png'%i)
 if p.is_file():print('STAGE35_SLIDER='+json.dumps({'file':p.name,'bytes':p.stat().st_size,'webp':p.with_suffix('.webp').is_file()}))
PY
"""
def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('NETANGELS_API_KEY missing')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels token missing')
 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage35-audit-read-') as d:
  private=d+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',private],check=True)
  pub=Path(private+'.pub').read_text().strip()
  resp=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'stage35-audit-'+os.getenv('GITHUB_RUN_ID','audit')}).encode(),headers)
  kid=resp.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',private,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],input=REMOTE,text=True,capture_output=True,timeout=150)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE35_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('Contact audit failed: '+p.stdout[-400:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH cleanup needs inspection')
if __name__=='__main__':run()
