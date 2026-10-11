#!/usr/bin/env python3
"""Guarded image optimization for homepage promotional slides without touching catalog.

Creates lower-bandwidth WebP slideshow copies and keeps PNG originals.
Copies active Smarty template to private backup, rolls back if smoke fails.
"""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
python3 - <<'PY'
import os,json,pathlib,shutil,subprocess,tempfile,time,re,urllib.request
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
site=root/'wa-data/public/site/themes/pureMegapolis42'
slider=site/'layouts/layout.slider.html'
images=site/'img/slider'
old=slider.read_text(encoding='utf-8')
needle='''<img class="slider__image" src="{$wa_parent_theme_url}{$_image}"{if !empty($_titles[$_image@index])} alt="{$_titles[$_image@index]}"{/if}>'''
if old.count(needle)!=1 or 'megapolis-webp-slider-2026' in old:
 raise RuntimeError('Slider template does not match audit, no writes')
image_list=[images/('slider_%02d.png'%n) for n in range(1,8)]
for p in image_list:
 if not p.is_file() or p.stat().st_size<100000:raise RuntimeError('Original slider image missing: '+p.name)
 if p.with_suffix('.webp').exists():raise RuntimeError('WebP asset already exists: '+p.name)
replacement='''{* megapolis-webp-slider-2026: original PNG remains fallback *}
                        <img class="slider__image" src="{$wa_parent_theme_url}{$_image}"{if strpos($_image, 'img/slider/slider_0') === 0} srcset="{$wa_parent_theme_url}{$_image|replace:'.png':'.webp'} 1x"{/if} width="2172" height="724" decoding="async"{if !empty($_titles[$_image@index])} alt="{$_titles[$_image@index]|escape}"{/if}>'''
new=old.replace(needle,replacement,1)
folder=root.parent/'storefront-backups'/('stage36-slider-webp-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700,parents=True,exist_ok=True)
backup=folder/'layout.slider.html'
shutil.copy2(str(slider),str(backup));os.chmod(str(backup),0o600)
created=[]
written=False
temp=pathlib.Path(tempfile.mkdtemp(prefix='.stage36-',dir=str(images)))
try:
 results=[]
 for img in image_list:
  target=img.with_suffix('.webp')
  st=img.stat()
  tmp=temp/target.name
  cp=subprocess.run(['cwebp','-quiet','-q','84','-m','5','-mt',str(img),'-o',str(tmp)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=55)
  if cp.returncode!=0 or not tmp.exists() or tmp.read_bytes()[:4]!=b'RIFF' or tmp.read_bytes()[8:12]!=b'WEBP':
   raise RuntimeError('WebP encode failed: '+img.name+' '+cp.stderr[-160:])
  if tmp.stat().st_size>st.st_size*0.83:
   raise RuntimeError('WebP saving too small to justify switching: '+img.name)
  os.chown(str(tmp),st.st_uid,st.st_gid)
  os.chmod(str(tmp),0o644)
  os.replace(str(tmp),str(target));created.append(target)
  results.append({'file':img.name,'png_bytes':st.st_size,'webp_bytes':target.stat().st_size})
 st=slider.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(slider.parent),prefix='.stage36-slider-',delete=False) as f:
  f.write(new);tmpfile=f.name
 os.chmod(tmpfile,st.st_mode&0o777)
 os.chown(tmpfile,st.st_uid,st.st_gid)
 os.replace(tmpfile,str(slider));written=True
 import urllib.error
 checks=[]
 for route in ('/','/category/kompyuternye-kresla/','/order/'):
  u='https://profikompany.ru'+route+'?stage36_check='+str(int(time.time()))
  p=subprocess.run(['curl','-ksSL','--max-time','32','-w','\n%{http_code}',u],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=40)
  page,status=p.stdout.rsplit('\n',1) if '\n' in p.stdout else ('','')
  has_source=('slider_01.webp' in page and 'srcset=' in page)
  if route=='/':
   ok=p.returncode==0 and status=='200' and has_source and page.count('srcset=')>=7
  else:ok=p.returncode==0 and status=='200'
  checks.append({'route':route,'http':status,'webp_in_html':has_source,'srcset_count':page.count('srcset='),'image_pos':page.find('slider_01.png'),'first_slider_markup':page[max(0,page.find('slider_01.png')-170):page.find('slider_01.png')+300] if route=='/' else '', 'ok':ok})
 if not all(c['ok'] for c in checks):
  raise RuntimeError('Public page smoke test failed: '+json.dumps(checks,ensure_ascii=False))
 for n in [1,4,7]:
  url='https://profikompany.ru/wa-data/public/site/themes/pureMegapolis42/img/slider/slider_%02d.webp'%n
  p=subprocess.run(['curl','-ksSI','--max-time','24',url],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=30)
  if ' 200' not in p.stdout.splitlines()[0]:raise RuntimeError('WebP delivery failed: '+url)
 saved=sum(x['png_bytes']-x['webp_bytes'] for x in results)
 print('STAGE36_APPLY='+json.dumps({'ok':True,'backup':str(backup),'saved_bytes':saved,'slides':results,'checks':checks},ensure_ascii=False))
except Exception as ex:
 if written:shutil.copy2(str(backup),str(slider))
 for p in reversed(created):
  if p.exists():p.unlink()
 print('STAGE36_ROLLBACK='+json.dumps({'reason':str(ex),'template_restored':written,'webp_files_removed':len(created)},ensure_ascii=False))
 raise
finally:
 shutil.rmtree(str(temp),ignore_errors=True)
PY
"""

def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('Missing NETANGELS_API_KEY secret')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
   urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels gateway token missing')
 hdr={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage36-slider-') as td:
  ssh=td+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
  pub=Path(ssh+'.pub').read_text().strip()
  row=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'storefront-stage36-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),hdr)
  kid=row.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
     '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
     input=REMOTE,text=True,capture_output=True,timeout=240)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE36_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('Slider optimization failed '+p.stdout[-1300:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,hdr)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH key cleanup needs review')
if __name__=='__main__':run()
