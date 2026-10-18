#!/usr/bin/env python3
"""Read only: inspect cache lifetime and compilation traces for canonical template."""
from stage52_disable_callback import execute

REMOTE = r"""set -eu
python3 - <<'PY'
import os,pathlib,json,hashlib,subprocess,time
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
index=root/'wa-data/public/site/themes/pureMegapolis42/index.html'
stat=index.stat()
out={'template_sha':hashlib.sha256(index.read_bytes()).hexdigest(),
     'template_mtime':stat.st_mtime,
     'template_age_sec':round(time.time()-stat.st_mtime),
     'template_has_pagination_fix':b'Only clean numbered category pages' in index.read_bytes(),
     'cache_paths':[],'compiled_sample':[],'count_matches':0}
cache=root/'wa-cache'
if cache.is_dir():
 out['cache_paths']=[str(p.relative_to(root)) for p in list(cache.iterdir())[:24]]
 searchdirs=[cache/'apps'/'site',cache/'apps'/'shop',cache/'templates',cache/'compiled']
 for d in searchdirs:
  if not d.is_dir():continue
  cmd=['find',str(d),'-type','f','-name','*.php','-mmin','-180']
  try:
   files=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,universal_newlines=True).communicate(timeout=8)[0].splitlines()
   out['compiled_sample'].append({'root':str(d.relative_to(root)),'recent_php_files':len(files)})
   for fname in files[:500]:
    try:
     path=pathlib.Path(fname)
     if path.stat().st_size>300000:continue
     body=path.read_bytes()
     if b'Scoped search indexing rules' in body or b'CANONICAL' in body and b'canonical' in body:
      out['count_matches']+=1
      if len(out.get('template_matches',[]))<6:
       matches=out.setdefault('template_matches',[])
       matches.append({'path':str(path.relative_to(root)),'mtime':round(path.stat().st_mtime),'has_new':b'Only clean numbered category pages' in body,'has_old':b'{$canonical|escape}' in body,'size':len(body)})
    except Exception:pass
  except Exception as e:out['compiled_sample'].append({'root':str(d),'error':str(e)[:60]})
print('CANONICAL_CACHE_AUDIT='+json.dumps(out,ensure_ascii=False),flush=True)
PY
"""
if __name__=="__main__":
    result=execute(REMOTE)
    for line in result.splitlines():
        if line.startswith('CANONICAL_CACHE_AUDIT='):print(line,flush=True)
