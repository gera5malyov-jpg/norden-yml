#!/usr/bin/env python3
"""Read-only trace inventory quantity binding for YML PRO 289."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
cd "$ROOT"
F="$(mktemp /tmp/yml289-options-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$p=$db->query('SELECT * FROM shop_yml_profiles WHERE id=289')->fetch();
$keys=array_keys($p);
echo 'YML289_DB_PROFILE_FIELDS='.json_encode($keys,JSON_UNESCAPED_UNICODE).PHP_EOL;
$c=json_decode($p['config'],true);
foreach($c as $k=>$v){
 if(preg_match('/(auth|login|pass|token|url|secret|key|api|feed|filename|file_path)/i',(string)$k))continue;
 if(is_array($v)) {$fields=array_keys($v);$output=array('kind'=>'array','keys'=>array_slice($fields,0,16));}
 elseif(is_bool($v) || $v===null || is_numeric($v)){$output=$v;}
 elseif(is_string($v) && strlen($v)<=70){$output=$v;}
 else {$output='[text]';}
 echo 'YML289_PROFILE_SETTING='.json_encode(array('key'=>$k,'value'=>$output),JSON_UNESCAPED_UNICODE).PHP_EOL;
}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
python3 - <<'PY'
import pathlib,re,json
base=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www/wa-apps/shop/plugins/yml')
files=[
 'lib/classes/import/handlers/shopYmlProductsHandler.class.php',
 'lib/classes/import/batch/shopYmlBatchWriter.class.php',
 'lib/classes/import/batch/shopYmlBatchStockStorage.class.php',
 'lib/classes/import/processors/shopYmlProductDataProcessor.class.php'
]
for rel in files:
 p=base/rel
 if not p.is_file():continue
 s=p.read_text(encoding='utf-8',errors='replace')
 # Only list literal field keys and method names when near handling "stock" or "count".
 result=[]
 for line,txt in enumerate(s.splitlines(),1):
  if not re.search(r'count|stocks|stock_id|stockCount|inventory|available|quantity',txt,re.I):continue
  if len(result)>=38:break
  method=re.search(r'(?:->|::)([A-Za-z_][A-Za-z0-9_]*)\s*\(',txt)
  keys=[]
  vars=re.findall(r'\$([a-zA-Z_][a-zA-Z0-9_]*)',txt)
  result.append({'line':line,'keys':keys[:6],'vars':list(dict.fromkeys(vars))[:9],'called':method.group(1) if method else None})
 print('YML289_STOCK_FLOW='+json.dumps({'file':rel,'references':result},ensure_ascii=False))
PY
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith("YML289_"):print(line,flush=True)
if "YML289_DB_PROFILE_FIELDS=" not in out:raise RuntimeError("No stock mapping data")
