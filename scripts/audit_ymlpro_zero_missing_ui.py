#!/usr/bin/env python3
"""Read-only UI options and safe metadata for YML PRO missing products and product codes."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
python3 - <<'PY'
import pathlib,re,json,os
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www/wa-apps/shop/plugins/yml')
pat=re.compile(r'исчез|пропав|отсутств|обну|нулев|сня(ть|тие) с продаж|склад|остат|генер|код товар|артикул|disappear|missing.*product|stock.*reset|stock.*zero|stock.*missing|unpublish|no.?offer|not.?found',re.I)
pathlist=[]
for f in (root/'templates').rglob('*'):
 if f.suffix.lower() not in ('.html','.tpl','.js') or not f.is_file():continue
 try:s=f.read_text(encoding='utf-8',errors='replace')
 except Exception:continue
 lines=s.splitlines()
 match=[]
 for i,line in enumerate(lines):
  if pat.search(line):
   plain=re.sub(r'<[^>]*>',' ',line)
   plain=re.sub(r'\{[^{}]*\}',' ',plain)
   plain=re.sub(r'&[a-zA-Z]+;',' ',plain)
   plain=re.sub(r'\s+',' ',plain).strip()
   configKeys=list(dict.fromkeys(re.findall(r'(?:settings|config|profile)\.([A-Za-z_]\w*)|name=["\x27]([\w\[\]\.-]+)',line)))
   match.append({'line':i+1,'label':plain[:145],'key_names':[a or b for a,b in configKeys][:6]})
 if match:pathlist.append({'file':str(f.relative_to(root)),'matches':match[:50]})
print('YMLOPT_TEMPLATES='+json.dumps(pathlist[:30],ensure_ascii=False))
# Never expose proprietary source: only filenames/method names/settings key names.
docs=[]
for f in (root/'lib').rglob('*.php'):
 if not f.is_file():continue
 try:s=f.read_text(encoding='utf-8',errors='replace')
 except Exception:continue
 matches=list(pat.finditer(s))
 if not matches:continue
 name=str(f.relative_to(root))
 if len(docs)>90:break
 if re.search('missing|stock|product|import|settings|filter|option',name,re.I):
  funs=re.findall(r'\bfunction\s+([A-Za-z_]\w*)\s*\(',s)
  keys=re.findall("['\\\"]([a-z][a-z0-9_]{3,65})['\\\"]",s)
  chosen=sorted(set(k for k in keys if pat.search(k)))
  docs.append({'file':name,'methods':[x for x in funs if pat.search(x)][:30],'keys':chosen[:35]})
print('YMLOPT_CLASSES='+json.dumps(docs[:80],ensure_ascii=False))
PY
F="$(mktemp /tmp/yml289-settings-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);
chdir('/home/web/vm-23f9aff9.na4u.ru/www');
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
try {
 $p=$db->query('SELECT id,config,name FROM shop_yml_profiles WHERE id=289')->fetch();
 $data=json_decode($p['config'],true);
 $keys=array();$vals=array();
 foreach($data as $key=>$value){
  if(preg_match('/(stock|reset|missing|disappear|offer|notfound|code|sku|article|auto|create|number|status|publish|zero|quantity|active|default)/i',$key)){
   $keys[]=$key;
   if(is_bool($value)||is_int($value)||is_float($value)||$value===null)$vals[$key]=$value;
   elseif(is_string($value)&&strlen($value)<80&&!preg_match('/(token|pass|key|url|secret)/i',$key))$vals[$key]=$value;
   elseif(is_array($value))$vals[$key]=array('type'=>'object_or_array','keys'=>array_slice(array_keys($value),0,25));
  }
 }
 echo 'YMLOPT_PROFILE='.json_encode(array('id'=>289,'name'=>$p['name'],'keys'=>$keys,'safe_values'=>$vals),JSON_UNESCAPED_UNICODE).PHP_EOL;
 $rows=$db->query('SELECT id,status,start_datetime,end_datetime,error_message FROM shop_yml_import_sessions WHERE profile_id=289 ORDER BY id DESC LIMIT 4')->fetchAll();
 echo 'YMLOPT_SESSIONS='.json_encode($rows,JSON_UNESCAPED_UNICODE).PHP_EOL;
} catch(Throwable $e){
 echo 'YMLOPT_PHP_ERROR='.get_class($e).' '.substr($e->getMessage(),0,130).PHP_EOL;
}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
out=execute(REMOTE)
for l in out.splitlines():
 if l.startswith('YMLOPT_'):print(l,flush=True)
if 'YMLOPT_TEMPLATES=' not in out:raise RuntimeError('UI settings inspection incomplete')
