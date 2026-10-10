#!/usr/bin/env python3
"""Domain-scoped font delivery improvement; preserve exact WOFF bytes.

Original inline-base64 CSS is untouched. Storefront index, new CSS and WOFFs
are backed up/rolled back atomically on smoke check failure. No catalog edits.
"""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json

REMOTE=r"""set -eu
python3 - <<'PY'
import base64,json,os,pathlib,re,shutil,subprocess,tempfile,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
theme=root/'wa-data/public/site/themes/pureMegapolis42'
index=theme/'index.html'
fonts=theme/'css/fonts'
source=fonts/'montserrat.min.css'
style=fonts/'montserrat.megapolis.css'
original=index.read_text(encoding='utf-8')
font_css=source.read_text(encoding='utf-8')
anchor='<link href="{$wa_real_theme_url}css/fonts/{$theme_settings.main__font}.min.css?v{$wa_theme_version}" rel="stylesheet" />'
if original.count(anchor)!=1:raise RuntimeError('Active font link changed - no writes')
if style.exists():raise RuntimeError('Optimized CSS already exists - no writes')
pattern=r'url\(data:application/x-font-woff;base64,([A-Za-z0-9+/=]+)\)'
blobs=re.findall(pattern,font_css)
if len(blobs)!=18 or font_css.count('@font-face')!=18:
 raise RuntimeError('Montserrat font count differs from audited original')
files=[]
updated_css=font_css
for i,b64 in enumerate(blobs,1):
 name='montserrat-megapolis-%02d.woff'%i
 path=fonts/name
 if path.exists():raise RuntimeError('Optimized font file already exists: '+name)
 data=base64.b64decode(b64,validate=True)
 if not data.startswith(b'wOFF') or not 1000<len(data)<100000:
  raise RuntimeError('Unexpected WOFF binary in position '+str(i))
 files.append((path,data))
 updated_css=updated_css.replace('url(data:application/x-font-woff;base64,'+b64+')',"url('"+name+"')",1)
if 'base64,' in updated_css or len(updated_css)>16000:
 raise RuntimeError('Generated CSS still contains inline font data')
updated_css=updated_css.replace('@font-face{','@font-face{font-display:swap;')
replacement=('''{if $wa->domainUrl() == 'https://profikompany.ru' && $theme_settings.main__font == "montserrat"}
        <link href="{$wa_real_theme_url}css/fonts/montserrat.megapolis.css?v=20261010c" rel="stylesheet" />
      {else}
        '''+anchor+'''
      {/if}''')
changed=original.replace(anchor,replacement)
if changed.count('montserrat.megapolis.css')!=1:
 raise RuntimeError('Theme modification mismatch')
backups=root.parent/'storefront-backups'
backups.mkdir(mode=0o700,parents=True,exist_ok=True)
folder=backups/('stage31-font-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700)
shutil.copy2(str(index),str(folder/'index.html'))
os.chmod(str(folder/'index.html'),0o600)
created=[]
index_written=False
stage=pathlib.Path(tempfile.mkdtemp(prefix='.font-stage31-',dir=str(fonts)))
try:
 source_st=source.stat()
 index_st=index.stat()
 for path,data in files:
  tmp=stage/path.name
  tmp.write_bytes(data)
  os.chmod(tmp,0o644)
  os.chown(str(tmp),source_st.st_uid,source_st.st_gid)
  if path.exists():raise RuntimeError('Unexpected font path race '+path.name)
  os.replace(str(tmp),str(path));created.append(path)
 css_tmp=stage/style.name
 css_tmp.write_text(updated_css,encoding='utf-8')
 os.chmod(css_tmp,0o644)
 os.chown(str(css_tmp),source_st.st_uid,source_st.st_gid)
 os.replace(str(css_tmp),str(style));created.append(style)
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(index.parent),prefix='.index-stage31-',delete=False) as f:
  f.write(changed);tmp=f.name
 os.chmod(tmp,index_st.st_mode)
 os.chown(tmp,index_st.st_uid,index_st.st_gid)
 os.replace(tmp,str(index));index_written=True
 checks=[]
 for route in ('/','/order/','/proizvoditeli/'):
  cmd=['curl','-ksSL','--max-time','30','-w','\n%{http_code}',
       'https://profikompany.ru'+route+'?font_stage31_check='+str(int(time.time()))]
  proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  out,err=proc.communicate(timeout=36)
  body,status=out.rsplit('\n',1) if '\n' in out else ('','')
  link_ok='montserrat.megapolis.css?v=20261010c' in body
  ok=proc.returncode==0 and status=='200' and link_ok
  checks.append({'path':route,'http':status,'optimized_css_link':link_ok,'ok':ok})
 if not all(c['ok'] for c in checks):raise RuntimeError('Live page smoke failed '+json.dumps(checks))
 p=subprocess.Popen(['curl','-ksSL','--max-time','25','-w','\n%{http_code}',
     'https://profikompany.ru/wa-data/public/site/themes/pureMegapolis42/css/fonts/montserrat.megapolis.css'],
     stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 output,_=p.communicate(timeout=32)
 body,status=output.rsplit('\n',1) if '\n' in output else ('','')
 if status!='200' or body.count('@font-face')!=18 or 'base64,' in body:
  raise RuntimeError('Optimized stylesheet HTTP validation failed '+status)
 print('STAGE31_APPLY='+json.dumps({'ok':True,'backup':str(folder),'font_files':len(files),
     'source_css_bytes':len(font_css.encode()),'new_css_bytes':len(updated_css.encode()),
     'saved_css_bytes':len(font_css.encode())-len(updated_css.encode()),
     'checks':checks},ensure_ascii=False))
except Exception as ex:
 if index_written:shutil.copy2(str(folder/'index.html'),str(index))
 for file in reversed(created):
  if file.exists():file.unlink()
 print('STAGE31_ROLLBACK='+json.dumps({'reason':str(ex),'restored_theme':index_written,'removed_generated_files':len(created)},ensure_ascii=False))
 raise
finally:
 shutil.rmtree(str(stage),ignore_errors=True)
PY
"""

def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('NETANGELS_API_KEY unavailable')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
  urllib.parse.urlencode({'api_key':key}).encode(),
  {'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels token unavailable')
 hdr={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='storefront-stage31-') as d:
  keypath=d+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',keypath],check=True)
  pub=Path(keypath+'.pub').read_text().strip()
  row=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
   'POST',json.dumps({'key':pub,'name':'storefront-stage31-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),hdr)
  kid=row.get('id')
  if not kid:raise RuntimeError('Unable to register temporary SSH key')
  try:
   p=subprocess.run(['ssh','-i',keypath,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
    '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
    input=REMOTE,text=True,capture_output=True,timeout=230)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE31_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('Font deploy failed '+p.stdout[-1600:]+' '+p.stderr[-450:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),
      'DELETE',None,hdr)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH key cleanup needs review')
if __name__=='__main__':run()
