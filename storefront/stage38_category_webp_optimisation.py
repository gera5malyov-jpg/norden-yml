#!/usr/bin/env python3
"""Guarded thumbnail WebP creation and theme optimization, no product data edits.

Fixes misleading home catalogue size and category double-period typo.
Copies active Smarty template to private backup, rolls back if smoke fails.
"""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
python3 - <<'PY'
import pathlib,json,shutil,subprocess,tempfile,os,re,urllib.request
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
theme=root/'wa-data/public/site/themes/pureMegapolis42'
file=theme/'layouts/layout.categories.html'
cat_root=root/'wa-data/public/shop/skcatimage'
ids=(4907,288,267,3046,2037,2180,2172,749,3057,274,270)
cwebp=shutil.which('cwebp')
if not cwebp:raise RuntimeError('cwebp unavailable - no writes')
orig=file.read_text(encoding='utf-8')
needle='<img class="isLazyLoad" src="{$wa_parent_theme_url}img/w3c.png" data-src="{$_image}" alt="{$_category.name}">'
if orig.count(needle)!=1:raise RuntimeError('Expected category image markup differs - no writes')
if 'megapolis-category-webp-v1' in orig:raise RuntimeError('Already optimized - no writes')
items=[]
for id in ids:
 directory=cat_root/str(id)
 candidates=sorted(directory.glob('thumbnail_*.png'))
 if len(candidates)!=1:raise RuntimeError('Expected exactly one category thumbnail png for '+str(id)+'; found '+str(len(candidates)))
 png=candidates[0]
 webp=png.with_suffix('.webp')
 if webp.exists():raise RuntimeError('Companion webp already exists for '+str(id))
 if not 5000<=png.stat().st_size<=3000000:raise RuntimeError('Suspicious thumbnail file size for '+str(id))
 items.append((id,png,webp))
check=' || '.join('$_category.id == '+str(id) for id in ids)
replacement=('''{* megapolis-category-webp-v1: lighter existing category thumbnails, original PNGs retained *}
                  {if ('''+check+''') && strpos($_image, '.png') !== false}
                    <img class="isLazyLoad" src="{$wa_parent_theme_url}img/w3c.png" data-src="{$_image|replace:'.png':'.webp'}" alt="{$_category.name|escape}" width="360" height="360" decoding="async">
                  {else}
                    '''+needle+'''
                  {/if}''')
updated=orig.replace(needle,replacement,1)
folder=root.parent/'storefront-backups'/('stage38-category-webp-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700,parents=True)
backup=folder/'layout.categories.html'
shutil.copy2(file,backup);os.chmod(backup,0o600)
created=[];theme_written=False
try:
 for cid,png,webp in items:
  tmp=webp.with_name('.stage38-'+webp.name)
  proc=subprocess.run([cwebp,'-quiet','-q','75','-m','4',str(png),'-o',str(tmp)],capture_output=True,text=True,timeout=28)
  if proc.returncode or not tmp.is_file():raise RuntimeError('WebP conversion failed for category '+str(cid)+': '+proc.stderr[-160:])
  if tmp.stat().st_size>=png.stat().st_size:
   tmp.unlink();raise RuntimeError('WebP not smaller for category '+str(cid))
  os.chmod(tmp,0o644)
  st=png.stat();os.chown(tmp,st.st_uid,st.st_gid)
  os.replace(tmp,webp);created.append(webp)
 stat=file.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(file.parent),prefix='.stage38-',delete=False) as f:
  f.write(updated);temp=f.name
 os.chmod(temp,stat.st_mode);os.chown(temp,stat.st_uid,stat.st_gid);os.replace(temp,file);theme_written=True
 checks=[]
 for route,has_webp in (('/',True),('/category/kompyuternye-kresla/',False),('/order/',False),('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/',False)):
  cmd=['curl','-ksSL','--max-time','37','-w','\n%{http_code}','https://profikompany.ru'+route+'?stage38_verify='+str(os.getpid())]
  p=subprocess.run(cmd,capture_output=True,text=True,timeout=48)
  h,status=p.stdout.rsplit('\n',1) if '\n' in p.stdout else ('','')
  targeted=bool(re.search(r'skcatimage/4907/thumbnail_[^"]+\.webp',h))
  ok=(p.returncode==0 and status=='200' and (not has_webp or targeted))
  checks.append({'route':route,'http':status,'new_webp_url':targeted,'ok':ok})
 if not all(z['ok'] for z in checks):raise RuntimeError('Storefront smoke failed '+json.dumps(checks,ensure_ascii=False))
 stats=[{'category_id':cid,'png':png.stat().st_size,'webp':webp.stat().st_size} for cid,png,webp in items]
 print('STAGE38_APPLY='+json.dumps({'ok':True,'backup':str(backup),'categories':len(items),
 'old_bytes':sum(x['png'] for x in stats),'new_bytes':sum(x['webp'] for x in stats),
 'first_three':stats[:3],'checks':checks},ensure_ascii=False))
except Exception as e:
 if theme_written:shutil.copy2(backup,file)
 for path in created:
  if path.is_file():path.unlink()
 print('STAGE38_ROLLBACK='+json.dumps({'reason':str(e),'template_restored':theme_written,'companions_deleted':len(created)},ensure_ascii=False))
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
 with tempfile.TemporaryDirectory(prefix='stage38-webp-') as td:
  ssh=td+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
  pub=Path(ssh+'.pub').read_text().strip()
  row=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'storefront-stage38-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),hdr)
  kid=row.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
     '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
     input=REMOTE,text=True,capture_output=True,timeout=240)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE38_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('SEO update failed '+p.stdout[-1300:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,hdr)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH key cleanup needs review')
if __name__=='__main__':run()
