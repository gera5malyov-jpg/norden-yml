#!/usr/bin/env python3
"""Production DB buffer increase, guarded by memory, transaction and storefront checks."""
from stage52_disable_callback import execute

REMOTE = r'''set -eu
python3 - <<'PY'
import os,re,sys,json,time,hashlib,pathlib,tempfile,subprocess,shutil,datetime
conf=pathlib.Path('/etc/mysql/my.cnf')
TARGET=1073741824
OLD=268435456
def emit(kind,data):
 print('PERF68_'+kind+'='+json.dumps(data,ensure_ascii=False),flush=True)
def run(cmd,timeout=55):
 p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 out,err=p.communicate(timeout=timeout)
 return p.returncode,out
def db(sql,timeout=95):
 code,out=run(['mysql','--protocol=socket','-uroot','-N','-B','-e',sql],timeout)
 return out.strip() if code==0 else None
def param(name):
 out=db("SHOW GLOBAL VARIABLES LIKE '"+name+"'")
 return out.split('\t',1)[1].strip() if out and '\t' in out else None
def memory_available():
 text=pathlib.Path('/proc/meminfo').read_text()
 m=re.search(r'(?m)^MemAvailable:\s*(\d+)\s*kB',text)
 return int(m.group(1))*1024 if m else 0
saved=None
backup=None
livechanged=False
configchanged=False
try:
 original=conf.read_bytes()
 source=original.decode('utf8')
 needle=r'(?m)^[ \t]*innodb_buffer_pool_size[ \t]*=[ \t]*256M[ \t]*$'
 matches=list(re.finditer(needle,source,re.I))
 if len(matches)!=1:raise RuntimeError('MySQL config not in expected state')
 header=list(re.finditer(r'(?m)^\s*\[([^\]]+)\]\s*$',source[:matches[0].start()]))
 group=header[-1].group(1).strip() if header else ''
 if group not in ('mysqld','mariadb','server','mysqld-10.3'):
  raise RuntimeError('Expected buffer setting is outside MariaDB server section')
 old=param('innodb_buffer_pool_size')
 if old is None or int(old)!=OLD or param('innodb_buffer_pool_chunk_size')!='134217728':
  raise RuntimeError('Live buffer has changed since audit')
 version=db('SELECT VERSION()')
 if not version or not version.startswith('10.3.23-MariaDB'):
  raise RuntimeError('Unexpected database version')
 emit('PREFLIGHT',{'check':1,'ok':True,'buffer_mb':256,'version':'10.3.23','config_section':group})
 mem=memory_available()
 fs=os.statvfs('/home/web')
 free_bytes=fs.f_bavail*fs.f_frsize
 if mem<8*1073741824 or free_bytes<7*1073741824:
  raise RuntimeError('Insufficient RAM or free disk')
 emit('PREFLIGHT',{'check':2,'ok':True,'memory_available_gb':round(mem/1073741824,2),'free_disk_gb':round(free_bytes/1073741824,2)})
 active=db('SELECT COUNT(*) FROM information_schema.innodb_trx')
 threads=db("SHOW GLOBAL STATUS LIKE 'Threads_running'")
 running=int(threads.split('\t')[-1]) if threads and '\t' in threads else 99
 if active is None or not active.isdigit() or int(active)>3 or running>14:
  raise RuntimeError('Live transactions too busy; skip resize')
 emit('PREFLIGHT',{'check':3,'ok':True,'active_transactions':int(active),'running_threads':running})
 path=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/storefront-backups')
 folder=path/('perf68-innodb-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ'))
 folder.mkdir(parents=True,mode=0o700)
 backup=folder/'my.cnf'
 shutil.copy2(str(conf),str(backup));os.chmod(str(backup),0o600)
 if backup.read_bytes()!=original:raise RuntimeError('Backup verification failed')
 emit('BACKUP',{'file':str(backup),'sha256':hashlib.sha256(original).hexdigest()})
 # MariaDB 10.3 supports online resize. This may temporarily wait on active transactions.
 resized=db('SET GLOBAL innodb_buffer_pool_size=1073741824',timeout=110)
 if resized is None:
  emit('BLOCKED',{'reason':'MariaDB refused dynamic resize; original configuration retained'})
  sys.exit(0)
 livechanged=True
 # Allow a background resize to complete before reading result.
 current=None
 for retry in range(22):
  current=param('innodb_buffer_pool_size')
  if current is not None and int(current)==TARGET:break
  time.sleep(1)
 if current is None or int(current)!=TARGET:raise RuntimeError('Live buffer readback failed')
 if memory_available()<6*1073741824:raise RuntimeError('Memory pressure after adjustment')
 emit('LIVE',{'buffer_mb':1024,'memory_available_gb':round(memory_available()/1073741824,2)})
 # Write precisely one MariaDB configuration parameter to survive restart.
 fresh=source[:matches[0].start()]+re.sub('256M','1G',matches[0].group(),flags=re.I)+source[matches[0].end():]
 if conf.read_bytes()!=original:raise RuntimeError('Concurrent configuration change')
 st=conf.stat()
 with tempfile.NamedTemporaryFile('wb',dir=str(conf.parent),prefix='.perf68-',delete=False) as f:
  f.write(fresh.encode('utf8')); tmp=f.name
 os.chmod(tmp,st.st_mode&0o777);os.chown(tmp,st.st_uid,st.st_gid)
 os.rename(tmp,str(conf));configchanged=True
 if conf.read_bytes()!=fresh.encode('utf8'):raise RuntimeError('Config readback mismatch')
 # The existing site must continue functioning; no anonymous cart/order mutations.
 urls=['/','/category/kompyuternye-kresla/','/category/kompyuternye-kresla/?page=2',
 '/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','/stul-prozrachnyy-pedrali-queen/',
 '/oplata/','/dostavka/','/o-kompanii/','/garantiya/','/order/']
 for i,u in enumerate(urls):
  result,html=run(['curl','-ksSL','--compressed','--max-time','24','-w','\n%{http_code}','https://profikompany.ru'+u],timeout=31)
  page,status=html.rsplit('\n',1) if '\n' in html else ('','')
  head=page.split('</head>',1)[0]
  ok=result==0 and status=='200' and '<title' in head and '+7 (812)' in page
  emit('TEST',{'number':i+1,'route':u,'ok':ok,'status':status})
  if not ok:raise RuntimeError('Storefront smoke check '+str(i+1)+' failed')
 if int(param('innodb_buffer_pool_size'))!=TARGET:raise RuntimeError('Buffer changed during validation')
 emit('DEPLOY',{'ok':True,'from_mb':256,'to_mb':1024,'backup':str(backup),
  'persist_after_restart':True,'db_restarted':False,'smoke_tests':10,'product_data_untouched':True})
except Exception as error:
 if configchanged and backup:
  shutil.copy2(str(backup),str(conf))
  emit('ROLLBACK',{'config_restored':True})
 if livechanged:
  restored=db('SET GLOBAL innodb_buffer_pool_size=268435456',timeout=110)
  value=param('innodb_buffer_pool_size')
  emit('ROLLBACK',{'runtime_restored':restored is not None and value is not None and int(value)==OLD})
 emit('ERROR',{'reason':str(error)[:320]})
 raise
PY
'''
if __name__=='__main__':
 text=execute(REMOTE)
 for line in text.splitlines():
  if line.startswith('PERF68_'):print(line,flush=True)
 if 'PERF68_DEPLOY=' not in text and 'PERF68_BLOCKED=' not in text:
  raise RuntimeError('Buffer tuning did not verify')
