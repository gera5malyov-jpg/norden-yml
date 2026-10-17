#!/usr/bin/env python3
"""Read-only audit of source product brand and active Yandex Product schema templates."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from stage52_disable_callback import execute
REMOTE=r"""set -eu
python3 - <<'PY'
import pathlib,json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
base=root/'wa-data/public/shop/themes/pureMegapolis42'
out={}
for filename in ('product.html','product.info.html'):
 p=base/filename
 if not p.is_file():continue
 lines=p.read_text(encoding='utf-8').splitlines()
 positions=[i for i,s in enumerate(lines) if ('itemtype="http://schema.org/Product"' in s or 'itemprop="brand"' in s or 'product-page__header' in s or 'product-overview__description' in s or 'itemprop="description"' in s or 'itemprop="offers"' in s)]
 out[filename]={'positions':[i+1 for i in positions[:15]],
                'excerpt':[{'line':i+1,'text':lines[i][:600]} for i in range(min(80,len(lines)))] if filename=='product.html' else [
                  {'line':max(1,i-2),'around':[x[:650] for x in lines[max(0,i-3):min(len(lines),i+4)]]} for i in positions[:6]]}
print('YANDEX_THEME_AUDIT='+json.dumps(out,ensure_ascii=False),flush=True)
PY
dbfile=$(mktemp /tmp/yandex-schema-brand-XXXXXX.php)
trap 'rm -f "$dbfile"' EXIT
cat > "$dbfile" <<'PHP'
<?php
error_reporting(E_ERROR);
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root); require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');$m=new waModel();$out=array();
try{$out['candidate_brand_features']=$m->query("SELECT id,code,name,type FROM shop_feature WHERE code LIKE '%brand%' OR code LIKE '%vendor%' OR name LIKE '%ренд%' OR name LIKE '%роизвод%' ORDER BY id LIMIT 40")->fetchAll();}catch(Throwable $e){$out['error_feature']=get_class($e);}
try{$out['sample_features']=$m->query("SELECT p.id,p.name,CHAR_LENGTH(p.description) description_len,p.type_id,f.id fid,f.code,f.name feature_name,pf.feature_value_id FROM shop_product p LEFT JOIN shop_product_features pf ON pf.product_id=p.id LEFT JOIN shop_feature f ON f.id=pf.feature_id WHERE p.id IN (1497800,1497801,1493900) AND (pf.feature_id IS NULL OR f.code LIKE '%brand%' OR f.code LIKE '%vendor%' OR f.name LIKE '%ренд%' OR f.name LIKE '%роизвод%') LIMIT 25")->fetchAll();}catch(Throwable $e){$out['error_sample']=get_class($e);}
try{$out['brand_feature_count_type96']=$m->query("SELECT f.code,f.name,COUNT(DISTINCT p.id) products FROM shop_product p JOIN shop_product_features pf ON pf.product_id=p.id JOIN shop_feature f ON f.id=pf.feature_id WHERE p.type_id=96 AND (f.code LIKE '%brand%' OR f.code LIKE '%vendor%' OR f.name LIKE '%ренд%' OR f.name LIKE '%роизвод%') GROUP BY f.id,f.code,f.name LIMIT 30")->fetchAll();}catch(Throwable $e){$out['error_brand_count']=get_class($e);}
echo 'YANDEX_BRAND_DB='.json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
?>
PHP
chmod 644 "$dbfile"
su -s /bin/bash web -c "php $dbfile"
"""
def main():
 output=execute(REMOTE)
 found=False
 for line in output.splitlines():
  if line.startswith(('YANDEX_THEME_AUDIT=','YANDEX_BRAND_DB=')):
   print(line,flush=True);found=True
 if not found:raise RuntimeError('No Yandex schema audit')
if __name__=='__main__':main()
