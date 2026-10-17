#!/usr/bin/env python3
"""Guarded fix of 4sis HTTP galleries on catalog listing cards only.

No Webasyst database calls and no physical media uploads.
Back up exact theme files, patch fixed-origin URL rewrites, smoke check, rollback on failure.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage52_disable_callback import execute

REMOTE = r"""set -eu
python3 - <<'PY'
import json,os,pathlib,shutil,subprocess,tempfile,time
from datetime import datetime,timezone

ROOT=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
THEME=ROOT/'wa-data/public/shop/themes/pureMegapolis42'
FILES=(THEME/'products.html',THEME/'list-thumbs.html')
OLD='http://5.35.127.160/images/'
NEW='https://profikompany.ru/4sis-images/'
PATCH="{$_product_image_src = str_replace('"+OLD+"', '"+NEW+"', $_product_image_src)}"
CATEGORY='https://profikompany.ru/category/stoly_1/stoly-zhurnalnye/'
MAIN='https://profikompany.ru/barselona-kofeynyy-stolik-iz-iskusstvennogo-rotanga-tsvet-korichnevyy/'
PROBE='https://profikompany.ru/4sis-images/1c/38342043352038452045372033452033-001.jpg'
existing={};new_text={}
for file in FILES:
    if not file.is_file():raise RuntimeError('Missing theme file '+str(file))
    data=file.read_text(encoding='utf-8')
    anchor='<div class="product-item{if !$_is_slider}'
    if data.count(anchor)!=1 or data.count('data-src="{$_product_image_src}"')!=1:
        raise RuntimeError('Template structure changed: '+file.name)
    if PATCH in data:raise RuntimeError('Template already has the exact gallery URL rewrite: '+file.name)
    if data.count("$_product_image_src")<3:
        raise RuntimeError('Missing expected image URL variable: '+file.name)
    pos=data.index(anchor)
    prefix=data[:pos]
    indent=prefix.rsplit('\n',1)[-1]
    if indent.strip():raise RuntimeError('Unexpected template whitespace at insertion: '+file.name)
    revised=data[:pos]+PATCH+'\n'+indent+data[pos:]
    if revised.count(PATCH)!=1:raise RuntimeError('Unexpected patch count')
    existing[file]=data
    new_text[file]=revised

backup_base=ROOT.parent/'storefront-backups'
backup_base.mkdir(mode=0o700,parents=True,exist_ok=True)
folder=backup_base/('4sis-list-gallery-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700)
backups={}
for file in FILES:
    backup=folder/(file.name+'.bak')
    shutil.copy2(file,backup)
    os.chmod(backup,0o600)
    backups[file]=backup

def command(args,timeout=45):
    return subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
      universal_newlines=True,timeout=timeout)
def atomic(file,data):
    st=file.stat()
    fd,tmp=tempfile.mkstemp(dir=str(file.parent),prefix='.4sis-list-gallery-')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(data)
        os.chmod(tmp,st.st_mode&0o777)
        os.chown(tmp,st.st_uid,st.st_gid)
        os.replace(tmp,str(file))
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
def check_page(url):
    r=command(['curl','-ksSL','--max-time','30','-w','\n%{http_code}',
      url+'?extimg_list_fix='+str(os.getpid())+'_'+str(time.time_ns())],timeout=39)
    body,status=r.stdout.rsplit('\n',1) if '\n' in r.stdout else ('','?')
    return {'http':status,'returncode':r.returncode,
        'rewritten':body.count('data-src="'+NEW),
        'old':body.count('data-src="'+OLD),
        'length':len(body)}
written=[]
try:
    # Concurrency guard: no write if someone else modified one of the files.
    for file in FILES:
        if file.read_text(encoding='utf-8')!=existing[file]:
            raise RuntimeError('Concurrent theme modification: '+file.name)
    for file in FILES:
        atomic(file,new_text[file]); written.append(file)

    results=[]
    for attempt in range(9):
        cat=check_page(CATEGORY)
        product=check_page(MAIN)
        result={'attempt':attempt,'category':cat,'product':product}
        results.append(result)
        if (cat['http']=='200' and cat['returncode']==0 and cat['rewritten']>=2
             and cat['old']==0 and product['http']=='200'):
            break
        time.sleep(2)
    final=results[-1]
    proxy=command(['curl','-ksSL','--max-time','25','-o','/dev/null',
      '-w','%{http_code}|%{content_type}|%{size_download}',PROBE],timeout=31)
    if (final['category']['http']!='200'
        or final['category']['returncode']!=0
        or final['category']['rewritten']<2
        or final['category']['old']!=0
        or final['product']['http']!='200'
        or proxy.returncode!=0
        or not proxy.stdout.startswith('200|image/jpeg|')):
        raise RuntimeError('Catalog patch smoke failed '+json.dumps({
          'last':final,'proxy':proxy.stdout,'proxy_err':proxy.stderr[-200:]}))
    print('FOURSIS_LIST_GALLERY_FIX='+json.dumps({
       'result':'success','files':[str(f.relative_to(ROOT)) for f in FILES],
       'backup_dir':str(folder),'smoke':final,
       'proxy':proxy.stdout,'checks':len(results)},ensure_ascii=False),flush=True)
except Exception as exc:
    for file in reversed(written):shutil.copy2(backups[file],file)
    print('FOURSIS_LIST_GALLERY_FIX='+json.dumps({
      'result':'rolled_back','backup_dir':str(folder),
      'restored':[str(x.relative_to(ROOT)) for x in written],
      'error':str(exc)[:1500]},ensure_ascii=False),flush=True)
    raise
PY
"""
def main():
    output=execute(REMOTE)
    if '"result": "success"' not in output and '"result":"success"' not in output:
        raise RuntimeError('No successful live catalog image smoke check')
if __name__=='__main__':
    main()
