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
 # Inspect Webasyst's hashed cache roots without full expensive traversal.
 levels=[cache]
 for depth in range(4):
  next_levels=[]
  for parent in levels[:50]:
   try:
    children=list(parent.iterdir())[:90]
   except Exception:
    continue
   subs=[c for c in children if c.is_dir()]
   files=[c for c in children if c.is_file()]
   path_label=str(parent.relative_to(root))
   if len(out.setdefault('cache_tree',[]))<125:
    out['cache_tree'].append({'path':path_label,'dirs':[c.name for c in subs[:25]],'file_samples':[c.name for c in files[:6]]})
   next_levels.extend(subs)
  levels=next_levels[:80]
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
# Target active hashed Webasyst/Shop-Script Smarty compilation directories.
out['smarty_inspection']=[]
for prefix in ('d0907d','d0907e'):
 for app in ('site','shop'):
  d=cache/prefix/'apps'/app/'templates'
  if not d.exists():continue
  row={'path':str(d.relative_to(root)),'subdirs':[],'recent_files':[],'canonical_sources':[]}
  try:
   row['subdirs']=[x.name for x in list(d.iterdir())[:25]]
   p=subprocess.Popen(['find',str(d),'-maxdepth','7','-type','f','-mmin','-180'],
                       stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,universal_newlines=True)
   paths=p.communicate(timeout=12)[0].splitlines()
   row['recent_count']=len(paths)
   for fname in paths[:2000]:
    path=pathlib.Path(fname)
    if len(row['recent_files'])<12:
     row['recent_files'].append(str(path.relative_to(d)))
    try:
     if not path.name.endswith('.php') or path.stat().st_size>300000:continue
     data=path.read_bytes()
     if b'canonical' not in data and b'CANONICAL' not in data:continue
     if len(row['canonical_sources'])<6:
      row['canonical_sources'].append({'path':str(path.relative_to(d)),
                                       'size':len(data),
                                       'age_sec':round(time.time()-path.stat().st_mtime),
                                       'has_old_source':b'{$canonical|escape}' in data,
                                       'has_new_source':b'Only clean numbered category pages' in data,
                                       'has_index_label':b'index.html' in data})
    except Exception:pass
  except Exception as e:row['error']=str(e)[:150]
  out['smarty_inspection'].append(row)
# Find compiled index.html variants and whether the canonical branch is stale.
out['compiled_index_files']=[]
for prefix in ('d0907d','d0907e'):
 for app in ('site','shop'):
  p=cache/prefix/'apps'/app/'templates'/'compiled'
  if not p.is_dir():continue
  command=['find',str(p),'-type','f','-name','*.file.index.html.php']
  try:
   found=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,universal_newlines=True).communicate(timeout=15)[0].splitlines()
   for fullname in found[:12]:
    file1=pathlib.Path(fullname);body=file1.read_text(encoding='utf-8',errors='replace')
    k=body.find('CANONICAL')
    if k<0:k=body.find('canonical')
    snippet=body[max(0,k-800):min(len(body),k+1350)] if k>=0 else ''
    out['compiled_index_files'].append({'path':str(file1.relative_to(root)),'mtime':round(file1.stat().st_mtime),'mtime_diff_to_source':round(file1.stat().st_mtime-index.stat().st_mtime),'contains_site_template_path':'wa-data/public/site/themes/pureMegapolis42/index.html' in body ,'snippet':snippet[:1700]})
  except Exception as e:out.setdefault('compiled_lookup_errors',[]).append(str(e)[:120])
print('CANONICAL_CACHE_AUDIT='+json.dumps(out,ensure_ascii=False),flush=True)
PY
"""
if __name__=="__main__":
    result=execute(REMOTE)
    for line in result.splitlines():
        if line.startswith('CANONICAL_CACHE_AUDIT='):print(line,flush=True)
