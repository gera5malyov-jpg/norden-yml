#!/usr/bin/env python3
"""Publish product Buy -> nonempty cart redirect after confirmed /cart/add/ success.

Backed-up atomic theme update, isolated JS asset, domain and route guard,
live smoke tests and external browser QA with rollback on failure.
No catalog, pricing, inventory or order writes.
"""
from __future__ import annotations
import base64,json,os,subprocess,sys,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json

REMOTE_DEPLOY=r"""set -eu
python3 - <<'PY'
import base64,json,os,pathlib,shutil,subprocess,tempfile
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
theme=root/'wa-data/public/shop/themes/pureMegapolis42'
template=theme/'product.html'
asset=theme/'js/megapolis-buy-to-cart-v1.js'
original=template.read_text(encoding='utf-8')
js=base64.b64decode('STAGE48_ASSET_BASE64').decode('utf-8')
needle='''          {* Product-only mobile buying shortcut, no catalog changes *}
          {if $wa->domainUrl() == 'https://profikompany.ru' && !empty($product.category_id)}
            <div id="megapolis-mobile-buy"'''
replacement='''          {* Product-only mobile buying shortcut, no catalog changes *}
          {if $wa->domainUrl() == 'https://profikompany.ru' && !empty($product.category_id)}
            {* megapolis-buy-to-cart-success-v1: never navigate until server accepted item *}
            <script src="{$wa_real_theme_url}js/megapolis-buy-to-cart-v1.js?v=20261011b" defer></script>
            <div id="megapolis-mobile-buy"'''
if original.count(needle)!=1 or 'megapolis-buy-to-cart-success-v1' in original:
 raise RuntimeError('Product template no longer matches preflight, no writes')
if asset.exists():raise RuntimeError('Destination JS asset already exists, no writes')
if 'data.status !== ' not in js or 'ajaxSuccess' not in js or "w.location.assign('/order/')" not in js:
 raise RuntimeError('Unexpected JavaScript asset content, no writes')
modified=original.replace(needle,replacement,1)
broot=root.parent/'storefront-backups'
broot.mkdir(mode=0o700,parents=True,exist_ok=True)
folder=broot/('stage48-buy-cart-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700)
backup=folder/'product.html'
shutil.copy2(str(template),str(backup));os.chmod(str(backup),0o600)
saved=[];modified_template=False
try:
 stat=template.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(template.parent),prefix='.stage48-product-',delete=False) as t:
  t.write(modified);tmp=t.name
 os.chmod(tmp,stat.st_mode);os.chown(tmp,stat.st_uid,stat.st_gid)
 asset_stat=(theme/'js/product.min.js').stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(asset.parent),prefix='.stage48-js-',delete=False) as t:
  t.write(js);js_tmp=t.name
 os.chmod(js_tmp,0o644);os.chown(js_tmp,asset_stat.st_uid,asset_stat.st_gid)
 os.replace(str(js_tmp),str(asset));saved.append(asset)
 os.replace(str(tmp),str(template));modified_template=True

 checks=[]
 paths=[
  ('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','categorized'),
  ('/stul-sevyn-bukle-kofeynyy-2-sht/','uncategorized'),
  ('/order/','cart'),
  ('/','home'),
 ]
 for path,kind in paths:
  p=subprocess.Popen(['curl','-ksSL','--max-time','35','-w','\n%{http_code}',
   'https://profikompany.ru'+path+'?stage48_test='+str(os.getpid())],
   stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  out,err=p.communicate(timeout=43)
  html,status=out.rsplit('\n',1) if '\n' in out else ('','')
  script_present=('megapolis-buy-to-cart-v1.js?v=20261011b' in html)
  ok=(p.returncode==0 and status=='200')
  if kind=='categorized':
   ok=ok and html.count('megapolis-buy-to-cart-v1.js?v=20261011b')==1 and 'id="megapolis-mobile-buy"' in html and 'class="product-add"' in html
  elif kind=='uncategorized':
   ok=ok and not script_present
  checks.append({'route':path,'http':status,'script':script_present,'ok':bool(ok)})
 asset_url='https://profikompany.ru/wa-data/public/shop/themes/pureMegapolis42/js/megapolis-buy-to-cart-v1.js?v=20261011b'
 p=subprocess.Popen(['curl','-ksSL','--max-time','20','-w','\n%{http_code}',asset_url],
  stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 out,err=p.communicate(timeout=28)
 body,status=out.rsplit('\n',1) if '\n' in out else ('','')
 checks.append({'route':'new_js','http':status,'matches':('Megapolis buy-to-cart v1' in body),'ok':bool(status=='200' and 'Megapolis buy-to-cart v1' in body)})
 if not all(c['ok'] for c in checks):
  raise RuntimeError('Storefront verification failed '+json.dumps(checks,ensure_ascii=False))
 print('STAGE48_DEPLOY='+json.dumps({'success':True,'backup':str(folder),'checks':checks},ensure_ascii=False))
except Exception as e:
 if modified_template:shutil.copy2(str(backup),str(template))
 for f in saved:
  if f.is_file():f.unlink()
 print('STAGE48_ROLLBACK='+json.dumps({'error':str(e),'template_restored':modified_template,'assets_deleted':len(saved)},ensure_ascii=False))
 raise
PY
"""

def remote_rollback(backup_folder):
 if not backup_folder.startswith('/home/web/vm-23f9aff9.na4u.ru/storefront-backups/stage48-buy-cart-'):
  raise RuntimeError('Unexpected backup location, refusing rollback')
 folder_literal=json.dumps(backup_folder)
 return ("""set -eu
python3 - <<'PY'
import pathlib,shutil,json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
theme=root/'wa-data/public/shop/themes/pureMegapolis42'
file=theme/'product.html'
asset=theme/'js/megapolis-buy-to-cart-v1.js'
folder=pathlib.Path("""+folder_literal+""")
back=folder/'product.html'
if not back.is_file():raise RuntimeError('Backup not present')
if 'megapolis-buy-to-cart-success-v1' not in file.read_text(encoding='utf-8'):
 raise RuntimeError('Live template differs - no automatic rollback')
shutil.copy2(str(back),str(file))
if asset.is_file():asset.unlink()
print('STAGE48_ROLLBACK='+json.dumps({'ok':True,'backup':str(folder),'removed_new_js':True}))
PY
""")

def execute(remote):
 secret=os.environ.get('NETANGELS_API_KEY','').strip()
 if not secret:raise RuntimeError('Missing NetAngels API key')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
  urllib.parse.urlencode({'api_key':secret}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('Gateway access unavailable')
 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage48-buy-') as d:
  ssh=d+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
  pub=Path(ssh+'.pub').read_text().strip()
  row=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
   'POST',json.dumps({'key':pub,'name':'storefront-stage48-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
  kid=row.get('id')
  if not kid:raise RuntimeError('NetAngels temporary SSH key unavailable')
  try:
   p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
    '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
    input=remote,text=True,capture_output=True,timeout=240)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE48_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('Remote operation failed '+p.stdout[-2500:]+' '+p.stderr[-650:])
   return p.stdout
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH key cleanup needs review',flush=True)

def main():
 state_file=Path('stage48-state.json')
 if '--rollback' in sys.argv:
  if not state_file.is_file():
   print('No deployed state exists; no rollback required')
   return
  state=json.loads(state_file.read_text())
  execute(remote_rollback(state['backup']))
  state_file.unlink()
  return
 js=(Path(__file__).parent/'assets/megapolis-buy-to-cart-v1.js').read_bytes()
 remote=REMOTE_DEPLOY.replace('STAGE48_ASSET_BASE64',base64.b64encode(js).decode('ascii'))
 stdout=execute(remote)
 applied=None
 for line in stdout.splitlines():
  if line.startswith('STAGE48_DEPLOY='):
   applied=json.loads(line.split('=',1)[1])
 if not applied or not applied.get('success'):raise RuntimeError('Could not verify remote deployment')
 state_file.write_text(json.dumps({'backup':applied['backup']}))
if __name__=='__main__':main()
