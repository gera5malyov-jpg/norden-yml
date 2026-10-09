#!/usr/bin/env python3
"""Narrow backed-up SEO title, description, and self-canonical fixes for 3 info pages."""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json

REMOTE=r"""set -eu
python3 - <<'PY'
import json,os,pathlib,shutil,subprocess,tempfile
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
p=root/'wa-data/public/site/themes/pureMegapolis42/index.html'
before=p.read_text(encoding='utf-8')
title='<title>{$wa->title()|escape}</title>'
desc='<meta name="description" content="{$wa->meta(\'description\')|escape}" />'
if before.count(title)!=1 or before.count(desc)!=1:
    raise RuntimeError('Theme title/description source changed; abort')
new_title='''{* Information page SEO labels: scoped to profikompany.ru only *}
    {$_info_title = ''}
    {$_info_description = ''}
    {if $wa->domainUrl() == 'https://profikompany.ru'}
      {$_seo_info_url = $wa->currentUrl(false, true)}
      {if $_seo_info_url == '/oplata/'}
        {$_info_title = 'Оплата заказа: способы и условия | Мегаполис'}
        {$_info_description = 'Условия оплаты заказов в интернет-магазине Мегаполис. Информация о покупке мебели с доставкой по Санкт-Петербургу и России.'}
      {/if}
      {if $_seo_info_url == '/dostavka/'}
        {$_info_title = 'Доставка мебели по Санкт-Петербургу и России | Мегаполис'}
        {$_info_description = 'Условия доставки мебели и предметов интерьера по Санкт-Петербургу и России. Порядок оформления и получения заказа в магазине Мегаполис.'}
      {/if}
      {if $_seo_info_url == '/privacy-policy/'}
        {$_info_title = 'Политика обработки персональных данных | Мегаполис'}
        {$_info_description = 'Политика обработки персональных данных магазина Мегаполис: цели обработки, права покупателей и способы связи с оператором.'}
      {/if}
    {/if}
    {if !empty($_info_title)}
      <title>{$_info_title|escape}</title>
    {else}
      <title>{$wa->title()|escape}</title>
    {/if}'''
new_desc='''{if !empty($_info_description)}
      <meta name="description" content="{$_info_description|escape}" />
    {else}
      <meta name="description" content="{$wa->meta('description')|escape}" />
    {/if}'''
after=before.replace(title,new_title,1).replace(desc,new_desc,1)
canonical_head='''          {if $wa->param('action') == 'category'}
            <link rel="canonical" href="{$wa->currentUrl(true, true)|escape}" />
          {/if}'''
canonical_new=canonical_head+'''
          {if $_seo_url == '/oplata/'}
            <link rel="canonical" href="{$wa->currentUrl(true, true)|escape}" />
          {/if}
          {if $_seo_url == '/dostavka/'}
            <link rel="canonical" href="{$wa->currentUrl(true, true)|escape}" />
          {/if}
          {if $_seo_url == '/privacy-policy/'}
            <link rel="canonical" href="{$wa->currentUrl(true, true)|escape}" />
          {/if}'''
if after.count(canonical_head)!=1:
    raise RuntimeError('Existing canonical block has changed; no writes')
after=after.replace(canonical_head,canonical_new,1)
backup_base=root.parent/'storefront-backups'
backup_base.mkdir(mode=0o700,parents=True,exist_ok=True)
backup_dir=backup_base/('seo-info-stage9-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
backup_dir.mkdir(mode=0o700)
backup=backup_dir/'index.html'
shutil.copy2(str(p),str(backup))
os.chmod(str(backup),0o600)
written=False
try:
    st=p.stat()
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(p.parent),prefix='.seo-info-',delete=False) as fp:
        fp.write(after)
        temp=fp.name
    os.chown(temp,st.st_uid,st.st_gid);os.chmod(temp,st.st_mode)
    os.replace(temp,str(p));written=True
    cases=[
      ('/oplata/','Оплата заказа: способы и условия | Мегаполис'),
      ('/dostavka/','Доставка мебели по Санкт-Петербургу и России | Мегаполис'),
      ('/privacy-policy/','Политика обработки персональных данных | Мегаполис')
    ]
    checks=[]
    for path,title_expected in cases:
        url='https://profikompany.ru'+path+'?info_seo_stage9_test=1'
        call=subprocess.Popen(['curl','-k','-sS','-L','--max-time','30','-w','\n%{http_code}',url],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
        out,err=call.communicate(timeout=38)
        html,status=out.rsplit('\n',1) if '\n' in out else ('','')
        head=html.split('</head>')[0]
        title_ok=('<title>'+title_expected+'</title>') in head
        desc_ok=('name="description"' in head and 'content=" — Мегаполис' not in head)
        canon_ok=head.count('<link rel="canonical"')==1 and 'href="https://profikompany.ru'+path+'"' in head
        ok=(call.returncode==0 and status=='200' and title_ok and desc_ok and canon_ok)
        checks.append({'path':path,'http':status,'title':title_ok,'description':desc_ok,'canonical':canon_ok,'ok':ok})
    if not all(x['ok'] for x in checks):
        raise RuntimeError('Live SEO smoke check failed '+json.dumps(checks,ensure_ascii=False))
    print('SEO_STAGE9_APPLY='+json.dumps({'status':'success','backup':str(backup),'checks':checks},ensure_ascii=False))
except Exception as exc:
    if written:shutil.copy2(str(backup),str(p))
    print('SEO_STAGE9_ROLLBACK='+json.dumps({'reason':str(exc),'restored':written},ensure_ascii=False))
    raise
PY
"""
def run():
 api=os.getenv('NETANGELS_API_KEY','').strip()
 if not api:raise RuntimeError('Missing NetAngels secret')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
   urllib.parse.urlencode({'api_key':api}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('Gateway token missing')
 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='seo-info-stage9-') as d:
  key=d+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',key],check=True)
  pub=Path(key+'.pub').read_text().strip()
  r=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',
    json.dumps({'key':pub,'name':'seo-info-stage9-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
  kid=r.get('id')
  if not kid:raise RuntimeError('SSH key could not be added')
  try:
   p=subprocess.run(['ssh','-i',key,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null',
      '-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
      input=REMOTE,text=True,capture_output=True,timeout=145)
   for l in p.stdout.splitlines():
    if l.startswith('SEO_STAGE9_'):print(l,flush=True)
   if p.returncode:raise RuntimeError('SEO info update failed: '+p.stdout[-1000:]+' '+p.stderr[-500:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
    print('temporary_ssh_key_removed=true')
   except Exception:
    print('WARNING: temporary SSH key removal needs review')
if __name__=='__main__':run()
