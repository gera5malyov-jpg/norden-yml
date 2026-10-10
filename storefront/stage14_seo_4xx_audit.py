#!/usr/bin/env python3
"""Stage 14: read-only SEO and 4xx bot crawl audit.

Only reads server logs, robots.txt source and theme links; never alters files.
"""
from __future__ import annotations
import json, os, subprocess, tempfile, urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE=r"""set -eu
python3 - <<'PY'
import glob,os,re,time,collections,json,pathlib,subprocess
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru')
www=root/'www'
paths=[
 '/var/log/nginx','/var/log/apache2','/var/log/httpd','/var/log',
 str(root/'logs'),str(root/'log'),str(www/'wa-log')
]
inventory=[]
for dr in paths:
 if not os.path.isdir(dr):continue
 try:
  arr=[]
  for name in os.listdir(dr)[:100]:
   path=os.path.join(dr,name)
   try:
    if os.path.isfile(path):
     stat=os.stat(path)
     if any(k in name.lower() for k in ('access','error','nginx','apache','http','php','404','seo')):
      arr.append({'file':name,'bytes':stat.st_size,'hours_ago':int((time.time()-stat.st_mtime)/3600)})
   except OSError:pass
  inventory.append({'dir':dr,'files':sorted(arr,key=lambda x:x['hours_ago'])[:25]})
 except OSError:pass
print('SEO_AUDIT_LOG_INVENTORY='+json.dumps(inventory,ensure_ascii=False))
# Read-only review of recent HTTP logs, bounded to tails. Never output IPs,
# cookies, query parameters, or user agents.
logfiles=[]
for dr in paths[:6]:
 if not os.path.isdir(dr):continue
 for p in glob.glob(os.path.join(dr,'*access*'))+glob.glob(os.path.join(dr,'*request*')):
  try:
   if os.path.isfile(p) and time.time()-os.path.getmtime(p)<14*86400:logfiles.append(p)
  except OSError:pass
found=[]
for logfile in list(dict.fromkeys(logfiles))[:14]:
 try:
  p=subprocess.Popen(['tail','-n','30000',logfile],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  out,err=p.communicate(timeout=15)
  logs=out.decode('utf-8','replace').splitlines()
  bot=collections.Counter()
  allcodes=collections.Counter()
  samples=collections.Counter()
  for line in logs:
   mat=re.search(r'"(?:GET|HEAD)\s+(\S+)\s+HTTP/\d(?:\.\d)?"\s+(\d{3})',line)
   if not mat:continue
   uri=mat.group(1).split('?')[0].split('#')[0]
   status=mat.group(2)
   allcodes[status]+=1
   if status.startswith('4'):
    # report only common paths without personal fields
    if not re.search(r'/my/|/order/|/checkout/|/login/|/account/|/profile/|/webasyst/',uri,re.I):
     samples[(status,uri[:150])]+=1
    if re.search(r'YandexBot|YandexMobileBot|YandexImages|YandexAccessibilityBot|Yandex',line,re.I):
     bot[(status,uri[:150])]+=1
  found.append({'log_name':os.path.basename(logfile),'lines_scanned':len(logs),
    'http_codes':allcodes.most_common(10),
    'top_4xx_paths':[{'code':code,'path':path,'hits':count} for ((code,path),count) in samples.most_common(25)],
    'yandex_4xx_paths':[{'code':code,'path':path,'hits':count} for ((code,path),count) in bot.most_common(40)]})
 except Exception as e:found.append({'log':os.path.basename(logfile),'error':type(e).__name__})
print('SEO_AUDIT_ACCESS_LOGS='+json.dumps(found,ensure_ascii=False))
robots=www/'robots.txt'
print('SEO_AUDIT_ROBOTS_SOURCE='+json.dumps({'static_file_exists':robots.is_file(),'static_size':robots.stat().st_size if robots.is_file() else None},ensure_ascii=False))
# Check whether broken routes are referenced in storefront template and CMS pages.
for frag in ('/contacts/','/shipping/','/oplata/','/dostavka/'):
 matches=[]
 for folder in ('wa-data/public/site/themes/pureMegapolis42','wa-data/public/shop/themes/pureMegapolis42'):
  base=www/folder
  if not base.exists():continue
  for file in base.rglob('*.html'):
   if file.stat().st_size>400000:continue
   try:
    data=file.read_text(encoding='utf-8',errors='replace')
    if frag in data:matches.append(str(file.relative_to(www)))
   except Exception:pass
 print('SEO_AUDIT_OLD_LINKS='+json.dumps({'target':frag,'theme_files':matches[:30]},ensure_ascii=False))
PY
"""

def run():
  key=os.environ.get('NETANGELS_API_KEY','').strip()
  if not key:raise RuntimeError('Missing NETANGELS_API_KEY secret')
  token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
      urllib.parse.urlencode({'api_key':key}).encode(),
      {'Content-Type':'application/x-www-form-urlencoded'}).get('token')
  if not token:raise RuntimeError('NetAngels gateway token missing')
  headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
  with tempfile.TemporaryDirectory(prefix='storefront-seo-audit-') as td:
    ssh=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
    pub=Path(ssh+'.pub').read_text().strip()
    entry=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
        'POST',json.dumps({'key':pub,'name':'storefront-seo-audit-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    kid=entry.get('id')
    if not kid:raise RuntimeError('Unable to register temporary SSH key')
    try:
      r=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
            '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
            input=REMOTE,text=True,capture_output=True,timeout=160)
      for line in r.stdout.splitlines():
        if line.startswith('SEO_AUDIT_'):print(line,flush=True)
      if r.returncode:
        raise RuntimeError('Storefront UX fix failed '+r.stdout[-1200:]+' '+r.stderr[-500:])
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
        print('temporary_ssh_key_removed=true',flush=True)
      except Exception:
        print('WARNING: temporary SSH cleanup needs review')
if __name__=='__main__':run()
