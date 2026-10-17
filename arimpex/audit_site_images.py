#!/usr/bin/env python3
"""Read-only live shop audit for Arimpex external images and native galleries."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -eu
dbfile=$(mktemp /tmp/arimpex-image-audit-XXXXXX.php)
trap 'rm -f "$dbfile"' EXIT
cat > "$dbfile" <<'PHP'
<?php
error_reporting(E_ERROR);
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();
$out=array();
try{$out['types']=$m->query("SELECT id,name FROM shop_type WHERE LOWER(name) LIKE '%arimpex%' OR LOWER(name) LIKE '%аримп%' ORDER BY id LIMIT 20")->fetchAll();}catch(Throwable $e){$out['types_error']=get_class($e).':'.$e->getMessage();}
try{
 $out['sku_count']=$m->query("SELECT COUNT(DISTINCT p.id) AS products, COUNT(*) AS skus FROM shop_product_skus s JOIN shop_product p ON p.id=s.product_id WHERE s.sku LIKE 'ARIMPEX-%'")->fetchAssoc();
 $out['by_type']=$m->query("SELECT p.type_id,COUNT(DISTINCT p.id) products,SUM(CASE WHEN p.summary LIKE '%[extimg]%' THEN 1 ELSE 0 END) summaries_extimg,SUM(CASE WHEN p.image_id IS NOT NULL AND p.image_id<>0 THEN 1 ELSE 0 END) native_image_id,SUM(CASE WHEN p.summary IS NULL OR p.summary='' THEN 1 ELSE 0 END) empty_summaries FROM shop_product p JOIN shop_product_skus s ON s.product_id=p.id WHERE s.sku LIKE 'ARIMPEX-%' GROUP BY p.type_id ORDER BY products DESC")->fetchAll();
 $out['examples']=$m->query("SELECT p.id,p.name,p.type_id,p.image_id,LEFT(p.summary,500) summary,s.sku FROM shop_product p JOIN shop_product_skus s ON s.product_id=p.id WHERE s.sku LIKE 'ARIMPEX-%' ORDER BY p.id DESC LIMIT 12")->fetchAll();
}catch(Throwable $e){$out['sku_error']=get_class($e).':'.$e->getMessage();}
try{
 $out['type96_count']=$m->query("SELECT COUNT(*) AS products,SUM(CASE WHEN summary LIKE '%[extimg]%' THEN 1 ELSE 0 END) extimg,SUM(CASE WHEN summary IS NULL OR summary='' THEN 1 ELSE 0 END) summary_empty,SUM(CASE WHEN image_id IS NOT NULL AND image_id<>0 THEN 1 ELSE 0 END) image_id_set FROM shop_product WHERE type_id=96")->fetchAssoc();
 $out['type96_samples']=$m->query("SELECT p.id,p.name,p.type_id,p.image_id,LEFT(p.summary,900) summary,p.id_1c,s.sku FROM shop_product p LEFT JOIN shop_product_skus s ON s.product_id=p.id WHERE p.type_id=96 ORDER BY p.id DESC LIMIT 14")->fetchAll();
 $out['type96_sku_pattern']=$m->query("SELECT LEFT(s.sku,15) prefix,COUNT(*) n FROM shop_product p JOIN shop_product_skus s ON s.product_id=p.id WHERE p.type_id=96 GROUP BY LEFT(s.sku,15) ORDER BY n DESC LIMIT 7")->fetchAll();
}catch(Throwable $e){$out['type96_error']=get_class($e).':'.$e->getMessage();}
echo 'ARIMPEX_SHOP_AUDIT='.json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
?>
PHP
chmod 644 "$dbfile"
su -s /bin/bash web -c "php $dbfile"
"""
def main():
 output=execute(REMOTE)
 found=[x for x in output.splitlines() if x.startswith('ARIMPEX_SHOP_AUDIT=')]
 if not found: raise RuntimeError('Live shop did not produce audit')
 for row in found: print(row,flush=True)
if __name__=='__main__':main()
