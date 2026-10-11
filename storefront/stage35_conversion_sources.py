#!/usr/bin/env python3
"""Read-only check of available image optimization tools and sales templates."""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
python3 - <<'PY'
import json, pathlib, re, shutil, subprocess, os
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
site=root/'wa-data/public/site/themes/pureMegapolis42'
shop=root/'wa-data/public/shop/themes/pureMegapolis42'
tools={}
for exe in ['cwebp','magick','convert','ffmpeg','php','python3']:
 tools[exe]=shutil.which(exe)
for module in ['PIL','cv2']:
 try:
  x=__import__(module);tools[module]=getattr(x,'__version__','yes')
 except Exception:tools[module]=None
try:
 p=subprocess.run(['php','-r','echo json_encode([extension_loaded("gd"),function_exists("imagewebp"),function_exists("imagecreatefrompng")]);'],capture_output=True,text=True,timeout=8)
 tools['php_gd_webp_png']=p.stdout
except Exception:pass
print('STAGE35_TOOLS='+json.dumps(tools,ensure_ascii=False))
files=[
 (site/'index.html',130,154),
 (site/'layouts/layout.slider.html',74,115),
 (site/'layouts/layout.header.html',1,80),
 (shop/'product.html',1,180),
 (shop/'product.info.html',1,170),
 (shop/'product.cart.html',1,220),
 (shop/'category.html',60,105),
 (shop/'products.html',1,180),
]
for path,start,end in files:
 if not path.exists():continue
 lines=path.read_text(encoding='utf-8',errors='replace').splitlines()
 arr=[{'n':i+1,'text':lines[i][:360]} for i in range(start-1,min(end,len(lines)))]
 print('STAGE35_SOURCE='+json.dumps({'path':str(path.relative_to(root)),'lines':arr},ensure_ascii=False))
for f in sorted((site/'img/slider').glob('slider_0[1-7].png')):
 print('STAGE35_SLIDER='+json.dumps({'file':f.name,'bytes':f.stat().st_size},ensure_ascii=False))
PY
"""
def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('NETANGELS_API_KEY missing')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels token missing')
 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage35-conversion-') as d:
  private=d+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',private],check=True)
  pub=Path(private+'.pub').read_text().strip()
  resp=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'stage35-conv-'+os.getenv('GITHUB_RUN_ID','audit')}).encode(),headers)
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
