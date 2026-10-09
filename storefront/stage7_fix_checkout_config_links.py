#!/usr/bin/env python3
"""Repair only the two audited links in the profikompany Shop-Script checkout config."""
from __future__ import annotations
import os,json,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r'''set -eu
python3 - <<'PY'
import json,os,pathlib,subprocess,shutil,tempfile
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
cfg=root/'wa-config/apps/shop/checkout2.php'
source=cfg.read_text(encoding='utf-8')
bad='---https://profikompany.ru/dostavka/---'
empty='<a href="">условиями обработки персональных данных</a>'
if source.count(bad)!=1 or source.count(empty)!=6:
    raise RuntimeError('Config source preflight counts differ; no changes')
bad_idx=source.index(bad)
empty_idxs=[i for i in range(len(source)) if source.startswith(empty,i)]
neighbors=[i for i in empty_idxs if 600<i-bad_idx<1500]
if len(neighbors)!=1 or neighbors[0]-bad_idx!=947:
    raise RuntimeError('Checkout config store block offsets differ; no changes')
new_customer=source.replace(bad,'/privacy-policy/',1)
old_blank_pos=neighbors[0]
shift=len('/privacy-policy/')-len(bad)
new_blank_pos=old_blank_pos+shift
if not new_customer.startswith(empty,new_blank_pos):
    raise RuntimeError('Only the matching shipping policy text may be edited')
replacement='<a href="/privacy-policy/" target="_blank" rel="noopener noreferrer">условиями обработки персональных данных</a>'
updated=new_customer[:new_blank_pos]+replacement+new_customer[new_blank_pos+len(empty):]
if updated.count(empty)!=5 or updated.count('/privacy-policy/')!=2 or updated.count(bad):
    raise RuntimeError('Config post-edit validation failed')
backup_root=root.parent/'storefront-backups'
backup_root.mkdir(mode=0o700,parents=True,exist_ok=True)
backup_dir=backup_root/('stage7-checkout-links-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
backup_dir.mkdir(mode=0o700)
backup=backup_dir/'checkout2.php'
shutil.copy2(str(cfg),str(backup))
os.chmod(str(backup),0o600)
written=False
try:
    st=cfg.stat()
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=str(cfg.parent),prefix='.checkout2-legal-',delete=False) as f:
       f.write(updated);tmp=f.name
    os.chmod(tmp,st.st_mode)
    os.chown(tmp,st.st_uid,st.st_gid)
    syntax=subprocess.Popen(['php','-l',tmp],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
    out,err=syntax.communicate(timeout=25)
    if syntax.returncode!=0:
        os.unlink(tmp)
        raise RuntimeError('PHP syntax validation failed')
    os.replace(tmp,str(cfg))
    written=True
    result=cfg.read_text(encoding='utf-8')
    if result!=updated:
        raise RuntimeError('Config readback failed')
    print('STOREFRONT_STAGE7_APPLY='+json.dumps({'ok':True,
      'file':str(cfg.relative_to(root)),'backup':str(backup),
      'other_storefront_empty_links_left_unmodified':5,
      'old_profikompany_link_removed':True},ensure_ascii=False))
except Exception as e:
    if written:shutil.copy2(str(backup),str(cfg))
    print('STOREFRONT_STAGE7_ROLLBACK='+json.dumps({'reason':str(e),'restored':written},ensure_ascii=False))
    raise
PY
'''
def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('NETANGELS_API_KEY missing')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
   urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels gateway inaccessible')
 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage7-checkout-') as d:
  ssh=d+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
  pub=Path(ssh+'.pub').read_text().strip()
  resp=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',
     json.dumps({'key':pub,'name':'checkout-policy-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
  kid=resp.get('id')
  if not kid:raise RuntimeError('Temporary SSH key unavailable')
  try:
    p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
      '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
      input=REMOTE,text=True,capture_output=True,timeout=155)
    for l in p.stdout.splitlines():
      if l.startswith('STOREFRONT_STAGE7_'):print(l,flush=True)
    if p.returncode:raise RuntimeError('Checkout config update failed '+p.stdout[-800:]+' '+p.stderr[-400:])
  finally:
    try:
      request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
      print('temporary_ssh_key_removed=true')
    except Exception:
      print('WARNING: temporary SSH key cleanup needs review')
if __name__=='__main__':run()
