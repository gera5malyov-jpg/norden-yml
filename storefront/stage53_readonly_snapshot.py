#!/usr/bin/env python3
"""Read only public theme sources and categorized product facts; no customer data."""
import base64, json
from pathlib import Path
from stage52_disable_callback import execute

REMOTE = r'''set -eu
python3 - <<'PY'
import pathlib,io,zipfile,base64
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
b=io.BytesIO()
with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
 for app in ('site','shop'):
  theme=root/('wa-data/public/'+app+'/themes/pureMegapolis42')
  for f in theme.rglob('*.html'):
   if f.is_file() and f.stat().st_size<400000:
    z.writestr(str(f.relative_to(root)),f.read_bytes())
  for f in (theme/'js').glob('*.js'):
   if f.is_file() and f.stat().st_size<400000 and any(x in f.name for x in ('product','categor','filter','cart','metrika','megapolis','slider')):
    z.writestr(str(f.relative_to(root)),f.read_bytes())
print('STAGE53_SOURCES='+base64.b64encode(b.getvalue()).decode())
PY
p=$(mktemp /tmp/storefront-stage53-XXXXXXXX.php)
trap 'rm -f "$p"' EXIT
cat >"$p" <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');$m=new waModel();
$out=[];
$out['categories']=$m->query("SELECT c.id,c.name,c.full_url,c.meta_title,c.meta_description,c.description,COUNT(p.id) products FROM shop_category c JOIN shop_product p ON p.category_id=c.id AND p.status=1 AND p.price>0 AND (p.count>0 OR p.count IS NULL) WHERE c.status=1 GROUP BY c.id ORDER BY products DESC LIMIT 40")->fetchAll();
$out['products']=[];
$rows=$m->query("SELECT p.id,p.name,p.url,p.category_id,p.price,p.count,p.image_id,p.description,p.summary,c.full_url AS category_url FROM shop_product p JOIN shop_category c ON p.category_id=c.id WHERE p.status=1 AND c.status=1 AND p.price>0 AND (p.count>0 OR p.count IS NULL) AND p.category_id IN (2195,288,2215,2267,267,275,1003) ORDER BY (p.description IS NULL OR TRIM(p.description)='') DESC,p.total_sales DESC,p.id DESC LIMIT 160")->fetchAll();
foreach($rows as $r){$p=new shopProduct($r['id']);$r['features']=$p->features;$r['skus']=[];foreach($p->skus as $sku){$r['skus'][]=array_intersect_key($sku,array_flip(['id','sku','name','price','count','available']));}$out['products'][]=$r;}
$out['pages']=$m->query("SELECT id,name,full_url,content FROM shop_page WHERE full_url IN ('dostavka/','oplata/') AND domain='profikompany.ru'")->fetchAll();
$out['paid_categories_90d']=$m->query("SELECT p.category_id,c.name,COUNT(DISTINCT o.id) paid_orders,SUM(i.price*i.quantity) item_revenue FROM shop_order o JOIN shop_order_params op ON op.order_id=o.id AND op.name='storefront' AND op.value LIKE '%profikompany.ru%' JOIN shop_order_items i ON i.order_id=o.id JOIN shop_product p ON p.id=i.product_id JOIN shop_category c ON c.id=p.category_id WHERE o.paid_date>=DATE_SUB(CURDATE(),INTERVAL 90 DAY) AND o.state_id NOT IN ('deleted','refunded') GROUP BY p.category_id,c.name ORDER BY item_revenue DESC LIMIT 15")->fetchAll();
echo 'STAGE53_FACTS='.json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
PHP
chmod 644 "$p"
su -s /bin/bash web -c "php '$p'"
'''

def main():
    result=execute(REMOTE)
    folder=Path('storefront-stage53-snapshot');folder.mkdir(exist_ok=True)
    seen=set()
    for line in result.splitlines():
        if line.startswith('STAGE53_SOURCES='):
            (folder/'sources.zip').write_bytes(base64.b64decode(line.split('=',1)[1]));seen.add('sources')
        elif line.startswith('STAGE53_FACTS='):
            data=json.loads(line.split('=',1)[1]);(folder/'facts.json').write_text(json.dumps(data,ensure_ascii=False,indent=2));seen.add('facts')
            print('STAGE53_SNAPSHOT='+json.dumps({'categories':len(data['categories']),'products':len(data['products']),'paid_categories':data['paid_categories_90d']},ensure_ascii=False))
    if seen!={'sources','facts'}:raise RuntimeError('Incomplete source snapshot')

if __name__=='__main__':main()
