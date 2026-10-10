#!/usr/bin/env python3
"""Read-only audit of current homepage theme files and critical asset sizes."""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
python3 - <<'PY'
import os,json,pathlib,re
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
site=root/'wa-data/public/site/themes/pureMegapolis42'
shop=root/'wa-data/public/shop/themes/pureMegapolis42'
needles=['slider_01','slider__image','montserrat.min.css','category-item__image','category-item','fa-solid.min.js','skcatimage']
for basedir in (site,shop):
 for file in basedir.rglob('*.html'):
  try:
   if file.stat().st_size>600000:continue
   raw=file.read_text(encoding='utf-8',errors='replace')
  except Exception:continue
  matching=[k for k in needles if k in raw]
  if not matching:continue
  lines=raw.splitlines()
  hits=[]
  for num,line in enumerate(lines):
   if any(k in line for k in matching):
    hits.append({'line':num+1,'text':line[:1100]})
  print('STAGE30_SOURCE='+json.dumps({'file':str(file.relative_to(root)),'needles':matching,
          'size':len(raw),'matching_lines':hits[:14]},ensure_ascii=False))
for file in [site/'index.html',site/'layouts/layout.categories.html',site/'layouts/layout.slider.html',shop/'category.html']:
 if file.is_file():
  for ix,line in enumerate(file.read_text(encoding='utf-8',errors='replace').splitlines()):
   if (file.name=='index.html' and 55<=ix+1<=152) or (file.name!='index.html' and ix<190):
    print('STAGE30_SNIPPET='+json.dumps({'file':str(file.relative_to(root)),'line':ix+1,'text':line[:350]},ensure_ascii=False))
for path in [
 site/'css/fonts/montserrat.min.css',
 site/'img/slider/slider_01.png',
 site/'img/slider/slider_02.png',
 site/'img/slider/slider_03.png',
 site/'img/slider/slider_04.png',
 site/'img/slider/slider_05.png',
 site/'img/slider/slider_06.png',
 site/'img/slider/slider_07.png'
]:
 if path.is_file():
  data={'file':str(path.relative_to(root)),'size':path.stat().st_size,'webp_exists':path.with_suffix('.webp').is_file()}
  if path.suffix=='.css':
   s=path.read_text(encoding='utf-8',errors='replace')
   data.update({'fontfaces':s.count('@font-face'),'base64_count':s.count('base64,'),'url_count':s.count('url('),
     'sample':s[:900]})
  print('STAGE30_ASSET='+json.dumps(data,ensure_ascii=False))
PY
"""
def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('NETANGELS_API_KEY missing')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels token missing')
 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage30-perf-read-') as d:
  private=d+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',private],check=True)
  pub=Path(private+'.pub').read_text().strip()
  resp=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'stage30-perf-'+os.getenv('GITHUB_RUN_ID','audit')}).encode(),headers)
  kid=resp.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',private,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],input=REMOTE,text=True,capture_output=True,timeout=150)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE30_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('Contact audit failed: '+p.stdout[-400:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH cleanup needs inspection')
if __name__=='__main__':run()
