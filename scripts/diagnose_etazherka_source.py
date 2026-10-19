#!/usr/bin/env python3
"""Read-only diagnostic for YML Pro profile 8 source linkage."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "storefront"))
from stage52_disable_callback import execute
remote=r"""set -eo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/etz-source-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$p=$db->query('SELECT * FROM shop_yml_profiles WHERE id=8')->fetch();
if (!$p) { echo "ETZ_DIAG=profile_missing\n"; exit(2); }
echo 'ETZ_DIAG=profile_columns:'.implode(',',array_keys($p))."\n";
foreach ($p as $k=>$v) {
 if (in_array($k,array('id','name','file','source','source_id','url','file_id','file_path'),true))
   echo 'ETZ_DIAG=profile_column:'.$k.'='.substr((string)$v,0,200)."\n";
}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
python3 - <<'PY'
from pathlib import Path
root=Path('wa-apps/shop/plugins/yml')
for name in ('lib/models/shopYmlProfiles.model.php','lib/config/db.php'):
 p=root/name
 if not p.is_file():continue
 s=p.read_text(encoding='utf-8',errors='replace').splitlines()
 print('ETZ_DIAG=codefile:'+name+' lines='+str(len(s)))
 for i,line in enumerate(s):
  if any(k in line.lower() for k in ('source','file','url','config')) and i<250:
   print('ETZ_DIAG=code:%s:%s:%s'%(name,i+1,line[:250]))
for p in root.rglob('*'):
 if p.suffix not in ('.php','.html','.js'):continue
 if p.stat().st_size>800000:continue
 try:s=p.read_text(encoding='utf-8',errors='replace')
 except Exception:continue
 if any(x in s for x in ('Изменить файл','Детали файла','Источник: URL')):
  print('ETZ_DIAG=ui_candidate:'+str(p))
PY
"""
output=execute(remote)
for line in output.splitlines():
 if line.startswith('ETZ_DIAG='): print(line,flush=True)
