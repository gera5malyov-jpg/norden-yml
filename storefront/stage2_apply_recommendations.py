#!/usr/bin/env python3
"""Safe, reversible production deployment: related-product relevance only."""
from __future__ import annotations
import json
import os
import subprocess
import tempfile
import urllib.parse
from stage1_readonly_audit import VM_ID,VM_IP,request_json

REMOTE=r'''set -eu
python3 - <<'PY'
import json,os,pathlib,shutil,subprocess,tempfile,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
f=root/'wa-data/public/shop/themes/pureMegapolis42/product.info.html'
before=f.read_text(encoding='utf-8')
target='{$upselling = $product->upSelling(12)}'
if before.count(target)!=1:
  raise RuntimeError('Upsell template differs from audited version; abort')
replacement="""{* Filter native Shop-Script upselling suggestions for product relevance. *}
{$upselling_candidates = $product->upSelling(24, true)}
{$upselling = []}
{foreach $upselling_candidates as $_related_product}
  {if (!empty($product.category_id) && !empty($_related_product.category_id) && $_related_product.category_id == $product.category_id) || (empty($product.category_id) && !empty($_related_product.type_id) && $_related_product.type_id == $product.type_id)}
    {$upselling[$_related_product.id] = $_related_product}
  {/if}
  {if count($upselling) >= 8}{break}{/if}
{/foreach}"""
updated=before.replace(target,replacement,1)
if updated==before or updated.count('{$upselling_candidates = $product->upSelling(24, true)}')!=1:
  raise RuntimeError('Unsafe or incomplete edit')
if updated.count('{/foreach}')!=before.count('{/foreach}')+1:
  raise RuntimeError('Smarty foreach count invalid')
backup_base=root.parent/'storefront-backups'
backup_base.mkdir(mode=0o700,parents=True,exist_ok=True)
backup_dir=backup_base/('stage2-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
backup_dir.mkdir(mode=0o700)
backup_file=backup_dir/'product.info.html'
shutil.copy2(str(f),str(backup_file))
os.chmod(str(backup_file),0o600)
written=False
try:
  stat=f.stat()
  with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(f.parent),prefix='.storefront-stage2-',delete=False) as t:
    t.write(updated);new_path=t.name
  os.chown(new_path,stat.st_uid,stat.st_gid)
  os.chmod(new_path,stat.st_mode)
  os.replace(new_path,str(f))
  written=True
  checks=[]
  for slug,not_expected,expected_price in [
    ('stul-sevyn-bukle-kofeynyy-2-sht/','bra-plass-chernoe/',14245),
    ('kreslo-mercury-lb-seraya-setka-matovyy-alyuminiy/','Кровать 649 MANHATTAN',55000),
    ('stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','bra-plass-chernoe/',1936)
  ]:
    url='https://profikompany.ru/'+slug+'?stage2_verify='+str(int(time.time()))
    p=subprocess.Popen(['curl','-k','-sS','-L','--max-time','40','-w','\n%{http_code}',url],
      stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
    out,err=p.communicate(timeout=48)
    page,status=out.rsplit('\n',1) if '\n' in out else ('','')
    ok=(p.returncode==0 and status=='200' and
        'data-price="'+str(expected_price)+'"' in page and
        not_expected not in page and
        'shop-fronted-fatal' not in page)
    checks.append({'product':slug,'status':status,'relevance_ok':not_expected not in page,'price_intact':('data-price="'+str(expected_price)+'"') in page,'ok':ok})
  if not all(x['ok'] for x in checks):
    raise RuntimeError('Product smoke test did not pass: '+json.dumps(checks,ensure_ascii=False))
  print('STOREFRONT_STAGE2_APPLY='+json.dumps({'status':'success','backup':str(backup_file),
    'modified_file':str(f.relative_to(root)),'checks':checks},ensure_ascii=False))
except Exception as e:
  if written:shutil.copy2(str(backup_file),str(f))
  print('STOREFRONT_STAGE2_ROLLBACK='+json.dumps({'reason':str(e),'restored':written,
    'backup':str(backup_file)},ensure_ascii=False))
  raise
PY
'''

def run():
  api_key=os.environ.get('NETANGELS_API_KEY','').strip()
  if not api_key:raise RuntimeError('Missing NETANGELS_API_KEY')
  gateway=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
    urllib.parse.urlencode({'api_key':api_key}).encode(),
    {'Content-Type':'application/x-www-form-urlencoded','User-Agent':'storefront-stage2-deploy/1.0'})
  token=gateway.get('token')
  if not token:raise RuntimeError('NetAngels gateway token missing')
  headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
  with tempfile.TemporaryDirectory(prefix='storefront-stage2-deploy-') as d:
    key=d+'/id_ed25519'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',key],check=True)
    pub=open(key+'.pub',encoding='utf-8').read().strip()
    registration=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
      'POST',json.dumps({'key':pub,'name':'storefront-related-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    key_id=registration.get('id')
    if not key_id:raise RuntimeError('Could not register short-lived SSH key')
    try:
      proc=subprocess.run(['ssh','-i',key,'-o','BatchMode=yes',
        '-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null',
        '-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
        input=REMOTE,text=True,capture_output=True,timeout=170)
      for line in proc.stdout.splitlines():
        if line.startswith('STOREFRONT_STAGE2_'):print(line,flush=True)
      if proc.returncode:raise RuntimeError('Deploy exit={} stderr={}'.format(proc.returncode,proc.stderr[-700:]))
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,key_id),
          'DELETE',None,headers)
        print('temporary_ssh_key_removed=true',flush=True)
      except Exception:
        print('WARNING: key cleanup needs attention',flush=True)

if __name__=='__main__':run()
