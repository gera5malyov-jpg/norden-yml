#!/usr/bin/env python3
"""Read only YML Pro source metadata for profile 8; do not log passwords/tokens."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "storefront"))
from stage52_disable_callback import execute
remote=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/etazherka-source-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$p=$db->query('SELECT * FROM shop_yml_profiles WHERE id=8')->fetch();
if(!$p){echo "ETZ_DIAG=profile_missing\n";exit(1);}
$safe=array('id','name','file_id','file','source','source_id','source_type','url','file_url','filename','path','file_path','original_filename');
echo 'ETZ_DIAG=profile_columns:'.implode(',',array_keys($p))."\\n";\nforeach($p as $k=>$v){if(in_array($k,$safe,true)){echo 'ETZ_DIAG=profile_column:'.$k.'='.substr((string)$v,0,200)."\n";}}
$c=json_decode($p['config'],true);
foreach(array('url','local_file','server_file','source_type','source','source_id','file_id','file','files','raw_image_links') as $k){
 $v=isset($c[$k])?$c[$k]:null;
 if(in_array($k,array('url','local_file','server_file'),true)){
    if(is_string($v) && strpos($v,'://')!==false){$parts=parse_url($v);$v=array('host'=>isset($parts['host'])?$parts['host']:'','path'=>isset($parts['path'])?$parts['path']:'');}
 }
 echo 'ETZ_DIAG=config:'.$k.'='.json_encode($v,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
}
$tables=$db->query("SHOW TABLES LIKE 'shop_yml%'")->fetchAll();
foreach($tables as $table){foreach($table as $name)echo 'ETZ_DIAG=table:'.$name."\n";}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
printf 'ETZ_DIAG=plugin_dirs\n'
find wa-apps/shop/plugins -maxdepth 1 -type d -iname '*yml*' | head -15 | sed 's/^/ETZ_DIAG=plugin_dir:/'
printf 'ETZ_DIAG=plugin_source_search\n'
grep -Rln --include='*.html' --include='*.php' --include='*.js' -e 'Детали файла' -e 'Изменить файл' -e 'Источник: URL' -e 'file_url' -e 'sourceFile' wa-apps/shop/plugins/yml* 2>/dev/null | head -40 | sed 's/^/ETZ_DIAG=source_candidate:/' || true
printf 'ETZ_DIAG=plugin_profile_model\\n'
python3 - <<'PY'\nfrom pathlib import Path\nfor name in ('wa-apps/shop/plugins/yml/lib/models/shopYmlProfiles.model.php','wa-apps/shop/plugins/yml/lib/config/db.php'):\n p=Path(name); s=p.read_text(encoding='utf-8',errors='replace').splitlines()\n print('ETZ_DIAG=codefile:'+name+' lines='+str(len(s)))\n for i,line in enumerate(s):\n  if any(k in line.lower() for k in ('source', 'file', 'url', 'config', 'profile')) and i<230:\n   print('ETZ_DIAG=code:'+str(i+1)+': '+line[:250])\nPY
"""
out=execute(remote)
for line in out.splitlines():
 if line.startswith("ETZ_DIAG="): print(line,flush=True)
