#!/usr/bin/env python3
"""Stage 1: safely fix homepage empty sets and seller legal address."""
import os, subprocess, tempfile, json, urllib.parse
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE = r"""set -eu
python3 - <<'PY'
import json,os,pathlib,shutil,subprocess,tempfile,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
base=root/'wa-data/public/site/themes/pureMegapolis42/layouts'
sets=base/'layout.sets.html'
contacts=base/'layout.contact.html'
s=sets.read_text(encoding='utf-8')
c=contacts.read_text(encoding='utf-8')
if s.count('{foreach key=key item=item from=$_sets}')!=2: raise RuntimeError('List structure mismatch')
if s.count('{$_set = $wa->shop->productSet($item)}')!=1: raise RuntimeError('List fetch mismatch')
if s.count('Product list is empty or not exists')!=1: raise RuntimeError('Empty warning mismatch')
if s.count('</section>')!=1: raise RuntimeError('Section count mismatch')
s=s.replace('{foreach key=key item=item from=$_sets}',
    '{foreach key=key item=_products from=$_valid_sets}\n            {$_item = $_sets[$key]}')
s=s.replace('{$item}','{$_item}')
s=s.replace('{if $key == 0}','{if $key == $_first_valid_set}')
s=s.replace('{$_set = $wa->shop->productSet($item)}','{$_set = $_products}')
start=s.index('{$_set = $_products}')
end=s.index('{/if}',start)+len('{/if}')
segment=s[start:end]
if 'Product list is empty or not exists' not in segment or 'products=$_set' not in segment:
    raise RuntimeError('Product panel structure mismatch')
include='{include file="'+chr(96)+'$wa_active_theme_path'+chr(96)+'/products.html" products=$_products _is_slider=$_is_slider inline}'
s=s[:start]+include+s[end:]
anchor='{$_is_slider = ( $theme_settings.products_lists__type == "slider" )}'
if s.count(anchor)!=1: raise RuntimeError('Theme settings mismatch')
preflight="""{$_valid_sets = []}
{$_first_valid_set = null}
{foreach key=key item=item from=$_sets}
  {$_items = $wa->shop->productSet($item)}
  {if !empty($_items)}
    {if empty($_valid_sets)}{$_first_valid_set = $key}{/if}
    {$_valid_sets[$key] = $_items}
  {/if}
{/foreach}"""
s=s.replace(anchor,anchor+'\n'+preflight,1)
section='<section class="products-lists section section--center">'
if s.count(section)!=1: raise RuntimeError('Section opening mismatch')
s=s.replace(section,'{if !empty($_valid_sets)}\n'+section,1)
closing='</section>\n\n{/strip}'
if s.count(closing)!=1: raise RuntimeError('Section closing mismatch')
s=s.replace(closing,'</section>\n{/if}\n\n{/strip}',1)
if 'Product list is empty or not exists' in s or s.count('$_valid_sets')<3:
    raise RuntimeError('List rewrite incomplete')
old_address="""{if !empty($_company_address)}
                <li><span>Адрес: </span>{$_company_address}</li>
              {/if}"""
new_address="""<li><span>Юридический адрес: </span>190005, г. Санкт-Петербург, ул. 7-я Красноармейская, д. 25, литера А, помещение 47-Н, помещ. 342</li>"""
if c.count(old_address)!=1: raise RuntimeError('Address template mismatch')
c=c.replace(old_address,new_address,1)
changes={sets:s,contacts:c}
backup_base=root.parent/'storefront-backups'
backup_base.mkdir(mode=0o700,parents=True,exist_ok=True)
backup_dir=backup_base/('stage1-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
backup_dir.mkdir(mode=0o700)
for f in changes:
    shutil.copy2(f,backup_dir/f.name)
    (backup_dir/f.name).chmod(0o600)
written=[]
try:
    for f,new in changes.items():
        stat=f.stat()
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(f.parent),prefix='.storefront-',delete=False) as t:
            t.write(new);tmp=pathlib.Path(t.name)
        os.chown(tmp,stat.st_uid,stat.st_gid)
        os.chmod(tmp,stat.st_mode)
        os.replace(tmp,f);written.append(f)
    url='https://profikompany.ru/?storefront_stage1_verify='+str(int(time.time()))
    proc=subprocess.run(['curl','-k','-sS','-L','--max-time','35','-w','\n%{http_code}',url],
                        text=True,capture_output=True,timeout=40)
    page,status=proc.stdout.rsplit('\n',1) if '\n' in proc.stdout else ('','')
    smoke={'status':status,'has_legal_address':'Юридический адрес:' in page,
           'has_empty_message':'Список товаров пустой или его не существует' in page,
           'page_length':len(page)}
    if proc.returncode!=0 or status!='200' or not smoke['has_legal_address'] or smoke['has_empty_message']:
        raise RuntimeError('Live smoke-test failure: '+json.dumps(smoke,ensure_ascii=False))
    print('STOREFRONT_APPLY='+json.dumps({'status':'success','files':[str(f.relative_to(root)) for f in written],
      'backup_path':str(backup_dir),'smoke':smoke},ensure_ascii=False))
except Exception as e:
    for f in written: shutil.copy2(backup_dir/f.name,f)
    print('STOREFRONT_ROLLBACK='+json.dumps({'reason':str(e),'files_restored':[str(f.relative_to(root)) for f in written]},ensure_ascii=False))
    raise
PY
"""

def run():
    key=os.environ.get('NETANGELS_API_KEY','').strip()
    if not key: raise RuntimeError('NETANGELS_API_KEY unavailable')
    token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
      urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
    if not token: raise RuntimeError('NetAngels auth unavailable')
    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
    with tempfile.TemporaryDirectory(prefix='storefront-stage1-') as d:
        ssh_key=os.path.join(d,'id_ed25519')
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh_key],check=True)
        pub=open(ssh_key+'.pub',encoding='utf-8').read().strip()
        result=request_json(f'https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/','POST',
            json.dumps({'key':pub,'name':'storefront-stage1-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
        key_id=result.get('id')
        if not key_id: raise RuntimeError('Temporary SSH key unavailable')
        try:
            args=['ssh','-i',ssh_key,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
                  '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s']
            result=subprocess.run(args,input=REMOTE,text=True,capture_output=True,timeout=160)
            for line in result.stdout.splitlines():
                if line.startswith('STOREFRONT_'): print(line,flush=True)
            if result.returncode: raise RuntimeError('Server script exit '+str(result.returncode)+' '+result.stderr[-450:])
        finally:
            try:
                request_json(f'https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{key_id}/','DELETE',None,headers)
                print('temporary_ssh_key_removed=true',flush=True)
            except Exception: print('WARNING: temporary SSH key cleanup needs review',flush=True)

if __name__=='__main__': run()
