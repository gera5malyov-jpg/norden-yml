#!/usr/bin/env python3
"""Read-only examine Deephouse import status and possible supplier feed snapshots."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
cd "$ROOT"
F="$(mktemp /tmp/deephouse-state-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$sessions=$db->query("SELECT id,start_datetime,end_datetime,status,exec_type FROM shop_yml_import_sessions WHERE profile_id=20 ORDER BY id DESC LIMIT 9")->fetchAll();
foreach($sessions as &$s)foreach($s as $k=>$v)if(is_int($k))unset($s[$k]);
$prof=$db->query('SELECT config FROM shop_yml_profiles WHERE id=20')->fetch();
$c=json_decode($prof['config'],true);
echo 'DEEPHOUSE_CURRENT_STATE='.json_encode(array('has_url'=>!empty($c['url']),
  'source_type'=>isset($c['source_type'])?$c['source_type']:null,'session_count'=>count($sessions),
  'sessions'=>$sessions,'mapping_count'=>(int)$db->query('SELECT COUNT(*) FROM shop_yml_products WHERE profile_id=20')->fetchField()),JSON_UNESCAPED_UNICODE)."\n";
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
python3 - <<'PY'
import os,json,datetime
root='/home/web/vm-23f9aff9.na4u.ru/www'
parents=[
root+'/wa-data/protected/shop/plugins/yml',
root+'/wa-data/private/shop/plugins/yml',
root+'/wa-cache/apps/shop/plugins/yml',
root+'/wa-log/shop/plugins',
root+'/wa-data/protected/shop/plugins',
root+'/wa-data/private/shop/plugins',
]
out=[]
for parent in parents:
 if not os.path.isdir(parent):
  out.append({'parent':parent.replace(root,'ROOT'),'exists':False})
  continue
 files=[]
 for d,dirs,fs in os.walk(parent):
  dirs[:]=[x for x in dirs if not x.startswith('.')][:22]
  for name in fs:
   path=os.path.join(d,name)
   try:st=os.stat(path)
   except:continue
   if st.st_size>0 and (name.endswith(('.xml','.yml','.zip','.gz','.json','.log')) or 'deephouse' in name.lower()):
    files.append({'basename':name[:90],'path_suffix':path[len(parent):][:130],'size':st.st_size,'modified_utc':datetime.datetime.utcfromtimestamp(st.st_mtime).strftime('%Y-%m-%dT%H:%M')})
   if len(files)>100:break
  if len(files)>100:break
 files.sort(key=lambda v:v['modified_utc'],reverse=True)
 out.append({'parent':parent.replace(root,'ROOT'),'exists':True,'files':files[:24]})
print('DEEPHOUSE_CACHE_INVENTORY='+json.dumps(out,ensure_ascii=False))
PY
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith("DEEPHOUSE_"):print(line,flush=True)
if "DEEPHOUSE_CURRENT_STATE=" not in out:raise RuntimeError("Incomplete inventory")
