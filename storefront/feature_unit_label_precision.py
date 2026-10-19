#!/usr/bin/env python3
"""Precise unit labels for confirmed Kenner millimetre dimensions, no data conversions."""
from stage52_disable_callback import execute
REMOTE=r'''set -eu
python3 - <<'PY'
import os,re,pathlib,shutil,tempfile,datetime,json,subprocess
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
d=root/'wa-data/public/shop/themes/pureMegapolis42'
files=[d/x for x in ('product.html','product.info.html','products.features.html')]
changes=[
 ("== 'РазмерГлубина'}Глубина{elseif","== 'РазмерГлубина'}Глубина, мм{elseif"),
 ("== 'РазмерВысота'}Высота{elseif","== 'РазмерВысота'}Высота, мм{elseif"),
 ("== 'РазмерШирина'}Ширина{elseif","== 'РазмерШирина'}Ширина, мм{elseif"),
]
before={};after={}
for p in files:
 s=p.read_text('utf8');before[p]=s
 if 'megapolis-feature-normalize-20261019' not in s:raise RuntimeError('Required feature normalization absent '+p.name)
 for old,new in changes:
  if s.count(old)<1:raise RuntimeError('Unexpected dimension mapping '+p.name+' '+old)
  s=s.replace(old,new)
 after[p]=s
folder=root.parent/'storefront-backups'/('kenner-units-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(parents=True,mode=0o700)
for i,p in enumerate(files):
 shutil.copy2(str(p),str(folder/(str(i)+'.bak')));os.chmod(str(folder/(str(i)+'.bak')),0o600)
written=[]
try:
 for p in files:
  if p.read_text('utf8')!=before[p]:raise RuntimeError('Concurrent theme edit')
  st=p.stat()
  with tempfile.NamedTemporaryFile('w',encoding='utf8',delete=False,dir=str(p.parent),prefix='.kenner-unit-') as f:
   f.write(after[p]);temp=f.name
  os.chmod(temp,st.st_mode&0o777);os.chown(temp,st.st_uid,st.st_gid)
  os.replace(temp,str(p));os.utime(str(p),None);written.append(p)
 for path,mode in [
   ('/stul-kenner-313sd-chernyy-zhemchug-e10-334-322382/','kenner'),
   ('/category/kompyuternye-kresla/','category'),
   ('/','home')]:
  proc=subprocess.Popen(['curl','-ksSL','--compressed','--max-time','30','-w','\n%{http_code}','https://profikompany.ru'+path],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  output,err=proc.communicate(timeout=38)
  page,status=output.rsplit('\n',1) if '\n' in output else ('','')
  ok=proc.returncode==0 and status=='200' and len(page)>2000
  if mode=='kenner':
   ok=ok and bool(re.search(r'Глубина, мм.{0,300}?590',page,re.S)) and bool(re.search(r'Высота, мм.{0,300}?760',page,re.S)) and 'Требуется сборка' in page
  row={'url':path,'http':status,'ok':bool(ok)}
  print('FEATURE_UNITS_TEST='+json.dumps(row,ensure_ascii=False),flush=True)
  if not ok:raise RuntimeError('Smoke failed '+path)
 print('FEATURE_UNITS_APPLIED='+json.dumps({'success':True,'files':len(files),'backup':str(folder),'product_values_changed':False},ensure_ascii=False),flush=True)
except Exception as ex:
 for p in reversed(written):
  if p.read_text('utf8')!=after[p]:raise RuntimeError('Concurrent change prevents rollback')
  shutil.copy2(str(folder/(str(files.index(p))+'.bak')),str(p));os.utime(str(p),None)
 print('FEATURE_UNITS_ROLLBACK='+json.dumps({'restored':len(written),'reason':str(ex)[:400]},ensure_ascii=False),flush=True)
 raise
PY
'''
if __name__=='__main__':
 out=execute(REMOTE)
 for line in out.splitlines():
  if line.startswith('FEATURE_UNITS_'):print(line,flush=True)
 if 'FEATURE_UNITS_APPLIED=' not in out:raise RuntimeError('No confirmed unit update')
