#!/usr/bin/env python3
"""Read-only check AF-<sku id> rule, collisions and existing automatic code generator."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute

REMOTE=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/yml289-code-guard-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
function emit($name,$v){echo $name.'='.json_encode($v,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES).PHP_EOL;}
$pattern=$db->query("SELECT COUNT(*) c, SUM(s.sku=CONCAT('AF-',s.id)) exact, SUM(s.sku LIKE 'AF-%') prefix,
 SUM(s.sku<>CONCAT('AF-',s.id) AND s.sku<>'') not_matching
 FROM shop_yml_products m JOIN shop_product_skus s ON s.product_id=m.shop_id
 WHERE m.profile_id=289 AND s.sku IS NOT NULL AND s.sku<>''")->fetch();
emit('AF289_PATTERN',$pattern);
$blank=$db->query("SELECT COUNT(*) c, COUNT(DISTINCT s.id) unique_skus, COUNT(DISTINCT m.shop_id) unique_products,
 MIN(s.id) min_id, MAX(s.id) max_id, MIN(p.create_datetime) earliest,MAX(p.create_datetime) latest
 FROM shop_yml_products m JOIN shop_product_skus s ON s.product_id=m.shop_id JOIN shop_product p ON p.id=m.shop_id
 WHERE m.profile_id=289 AND (s.sku IS NULL OR s.sku='')")->fetch();
emit('AF289_BLANK',$blank);
$collisions=$db->query("SELECT COUNT(*) c FROM shop_yml_products m JOIN shop_product_skus s ON s.product_id=m.shop_id
 JOIN shop_product_skus existing ON existing.sku=CONCAT('AF-',s.id)
 WHERE m.profile_id=289 AND (s.sku IS NULL OR s.sku='')")->fetchField();
emit('AF289_PROPOSED_COLLISIONS',array('existing_sku_collision_count'=>(int)$collisions));
$idx=$db->query("SHOW INDEX FROM shop_product_skus")->fetchAll();
$out=array();foreach($idx as $i){$name=$i['Key_name'];if(!isset($out[$name]))$out[$name]=array('unique'=>(int)$i['Non_unique']===0,'fields'=>array());$out[$name]['fields'][]=$i['Column_name'];}
emit('AF289_SKU_INDEXES',$out);
$other=$db->query("SELECT COUNT(*) total, SUM(CASE WHEN sku IS NULL OR sku='' THEN 1 ELSE 0 END) blank,
 SUM(CASE WHEN sku LIKE 'AF-%' THEN 1 ELSE 0 END) af_count FROM shop_product_skus")->fetch();
emit('AF289_GLOBAL_STATS',$other);
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
python3 - <<'PY'
import pathlib,json,re
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
folders=[root/'wa-apps/shop/plugins',root/'wa-apps/shop/lib']
hits=[];checked=0
for folder in folders:
 if not folder.is_dir():continue
 for path in folder.rglob('*.php'):
  checked+=1
  try:
   if path.stat().st_size>1200000:continue
   s=path.read_text(encoding='utf-8',errors='replace')
  except Exception:continue
  patterns=[('af_literal',r'''['"]AF-['"]'''),('sku_autofill',r'\b(?:generateSku|autoSku|skuCode|setSku|generateCode|updateSkuCode|sku_autofill)\b')]
  matched=[tag for tag,regex in patterns if re.search(regex,s,re.I)]
  if matched:hits.append({'file':str(path.relative_to(root)),'markers':matched})
  if len(hits)>80:break
  if checked>16000:break
print('AF289_CODE_SEARCH='+json.dumps({'php_files_examined':checked,'matches':hits[:80]},ensure_ascii=False))
PY
"""
r=execute(REMOTE)
for line in r.splitlines():
 if line.startswith("AF289_"):print(line,flush=True)
if "AF289_PATTERN=" not in r:raise RuntimeError("No SKU pattern result")
