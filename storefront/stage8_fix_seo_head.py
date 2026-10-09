#!/usr/bin/env python3
"""Reversible, scoped SEO head adjustments for profikompany.ru."""
from __future__ import annotations
import os,json,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json

REMOTE=r"""set -eu
python3 - <<'PY'
import json,os,pathlib,shutil,subprocess,tempfile,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
layout=root/'wa-data/public/site/themes/pureMegapolis42/index.html'
before=layout.read_text(encoding='utf-8')
anchor='''    {* CANONICAL *}
    {if !empty($canonical)}
      <link rel="canonical" href="{$canonical|escape}" />
    {/if}'''
if before.count(anchor)!=1:
    raise RuntimeError('SEO head structure changed - deployment cancelled')
addition='''    {* Scoped search indexing rules; preserve product and category URLs *}
    {if $wa->domainUrl() == 'https://profikompany.ru'}
      {$_seo_url = $wa->currentUrl(false, true)}
      {if $_seo_url == '/order/'}
        <meta name="robots" content="noindex,follow" />
      {/if}
      {if empty($canonical)}
        {if $_seo_url == '/'}
          <link rel="canonical" href="{$wa->currentUrl(true, true)|escape}" />
        {/if}
        {if $wa->param('action') == 'product'}
          <link rel="canonical" href="{$wa->currentUrl(true, true)|escape}" />
        {/if}
        {if $wa->param('action') == 'category'}
          {if $wa->currentUrl(false) == $_seo_url}
            <link rel="canonical" href="{$wa->currentUrl(true, true)|escape}" />
          {/if}
        {/if}
      {/if}
    {/if}'''
edited=before.replace(anchor,anchor+'\n'+addition,1)
backupbase=root.parent/'storefront-backups'
backupbase.mkdir(mode=0o700,parents=True,exist_ok=True)
backupdir=backupbase/('seo-stage8-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
backupdir.mkdir(mode=0o700)
backup=backupdir/'index.html'
shutil.copy2(str(layout),str(backup))
os.chmod(str(backup),0o600)
written=False
try:
    st=layout.stat()
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(layout.parent),prefix='.storefront-seo-',delete=False) as fp:
        fp.write(edited);temporary=fp.name
    os.chown(temporary,st.st_uid,st.st_gid)
    os.chmod(temporary,st.st_mode)
    os.replace(temporary,str(layout))
    written=True
    cases=[
      ('/',False,True),
      ('/category/kompyuternye-kresla/',False,True),
      ('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/',False,True),
      ('/order/',True,False)
    ]
    checks=[]
    for path,expect_noindex,expect_canonical in cases:
      u='https://profikompany.ru'+path+'?seo_stage8_check=1'
      cmd=['curl','-k','-sS','-L','--max-time','30','-w','\n%{http_code}',u]
      p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
      out,err=p.communicate(timeout=38)
      page,status=out.rsplit('\n',1) if '\n' in out else ('','')
      head=page.split('</head>')[0]
      robots=head.count('<meta name="robots" content="noindex,follow"')
      canonical=head.count('<link rel="canonical"')
      wanted='href="https://profikompany.ru'+path+'"'
      valid=(p.returncode==0 and status=='200' and
             (robots==1)==expect_noindex and (canonical==1)==expect_canonical and
             (not expect_canonical or wanted in head))
      checks.append({'path':path,'http_status':status,'robots_count':robots,
         'canonical_count':canonical,'canonical_target_correct':wanted in head,'ok':valid})
    if not all(x['ok'] for x in checks):
        raise RuntimeError('SEO smoke test failed '+json.dumps(checks,ensure_ascii=False))
    print('SEO_STAGE8_APPLY='+json.dumps({'status':'success','file':str(layout.relative_to(root)),
      'backup':str(backup),'checks':checks},ensure_ascii=False))
except Exception as e:
    if written:shutil.copy2(str(backup),str(layout))
    print('SEO_STAGE8_ROLLBACK='+json.dumps({'reason':str(e),'restored':written},ensure_ascii=False))
    raise
PY
"""

def run():
 key=os.getenv('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('NetAngels secret missing')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
   urllib.parse.urlencode({'api_key':key}).encode(),
   {'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('Gateway token unavailable')
 h={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='seo-stage8-') as d:
  priv=d+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',priv],check=True)
  pub=Path(priv+'.pub').read_text().strip()
  data=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
    'POST',json.dumps({'key':pub,'name':'seo-stage8-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),h)
  kid=data.get('id')
  if not kid:raise RuntimeError('SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',priv,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
     '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
     input=REMOTE,text=True,capture_output=True,timeout=160)
   for line in p.stdout.splitlines():
    if line.startswith('SEO_STAGE8_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('SEO deployment failed '+p.stdout[-1200:]+' '+p.stderr[-500:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),
      'DELETE',None,h)
    print('temporary_ssh_key_removed=true')
   except Exception:print('WARNING: SSH key cleanup needs review')

if __name__=='__main__':run()
