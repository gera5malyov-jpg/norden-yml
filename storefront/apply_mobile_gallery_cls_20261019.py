#!/usr/bin/env python3
"""Safe, narrow storefront CSS deploy. No database interaction."""
from stage52_disable_callback import execute
REMOTE = r'''set -eu
python3 - <<'PY'
import hashlib, pathlib, os, shutil, json, tempfile, datetime, subprocess
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
file=root/'wa-data/public/site/themes/pureMegapolis42/index.html'
text=file.read_text('utf-8')
old='    <meta name="viewport" content="width=device-width, initial-scale=1" />'
mark='megapolis-gallery-cls-20261019'
css="""\n    {if $wa->domainUrl() == 'https://profikompany.ru' && $wa->param('action') == 'product'}
    <style id="megapolis-gallery-cls-20261019">
    {literal}
    @media(max-width:767.98px){
    .product-gallery--extimg .product-gallery__main{aspect-ratio:1/1;overflow:hidden}
    .product-gallery--extimg .product-gallery__main img{max-height:100%;object-fit:contain}
    }
    {/literal}
    </style>
    {/if}"""
if text.count(old)!=1 or mark in text:raise RuntimeError('Guard failed; no writes')
before=file.read_bytes()
print('GALLERY_PREFLIGHT='+json.dumps({'ok':True,'source_sha256':hashlib.sha256(before).hexdigest()}),flush=True)
folder=root.parent/'storefront-backups'/('gallery-cls-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ'))
folder.mkdir(mode=0o700,parents=True)
backup=folder/'index.html'
shutil.copy2(str(file),str(backup))
if backup.read_bytes()!=before:raise RuntimeError('Backup invalid')
print('GALLERY_BACKUP='+json.dumps({'path':str(backup)}),flush=True)
new=text.replace(old,old+css,1).encode('utf-8')
tmp=None
written=False
try:
 st=file.stat()
 with tempfile.NamedTemporaryFile('wb',dir=str(file.parent),delete=False) as fh:
  fh.write(new);tmp=fh.name
 os.chmod(tmp,st.st_mode & 0o777);os.chown(tmp,st.st_uid,st.st_gid)
 if file.read_bytes()!=before:raise RuntimeError('Concurrent edit')
 os.replace(tmp,str(file));tmp=None;written=True
 cases=['/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','/stul-prozrachnyy-pedrali-queen/','/','/category/kompyuternye-kresla/','/category/kompyuternye-kresla/?page=2','/oplata/','/dostavka/','/o-kompanii/','/search/?query=%D0%91%D0%B8%D1%81%D1%82%D1%80%D0%BE','/order/']
 for i,url in enumerate(cases):
  p=subprocess.run(['curl','-ksSL','--max-time','32','-w','\n%{http_code}','https://profikompany.ru'+url],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=40)
  page,status=p.stdout.rsplit('\n',1)
  head=page.split('</head>',1)[0]
  ok=p.returncode==0 and status=='200' and (mark in head)==(i<2)
  print('GALLERY_TEST='+json.dumps({'index':i+1,'ok':ok,'http':status}),flush=True)
  if not ok:raise RuntimeError('Smoke case '+str(i+1))
 if file.read_bytes()!=new:raise RuntimeError('Source changed during validation')
 print('GALLERY_DEPLOY='+json.dumps({'ok':True,'tests':10,'backup':str(backup)}),flush=True)
except Exception as e:
 if written and file.read_bytes()==new:
  shutil.copy2(str(backup),str(file))
  print('GALLERY_ROLLBACK='+json.dumps({'restored':True,'reason':str(e)}),flush=True)
 raise
PY
'''
if __name__=='__main__':
 out=execute(REMOTE)
 for line in out.splitlines():
  if line.startswith(('GALLERY_PREFLIGHT=','GALLERY_BACKUP=','GALLERY_TEST=','GALLERY_DEPLOY=','GALLERY_ROLLBACK=')): print(line,flush=True)
 if 'GALLERY_DEPLOY=' not in out:raise RuntimeError('Not verified')
