#!/usr/bin/env python3
"""One SKU only: repair Riva description and specs, never prices/stocks/images."""
import base64
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "storefront"))
from stage52_disable_callback import execute

from supplier_engine.runner import fetch_source, normalize
from supplier_engine.adapters import load_xml_yml
from webasyst.client import WebasystClient

SKU = "УЧ-00001998"
PRODUCT_ID = "1489551"
SKU_ID = "31668689"
TYPE_ID = 136
SUPPLIER_ID = 16


def run_php(code, parameter=None):
    payload = base64.b64encode(json.dumps(parameter or {}, ensure_ascii=False).encode("utf-8")).decode("ascii")
    remote = (
        "set -eu\n"
        "cat > /tmp/riva_uch1998_once.php <<'PHP'\n"
        "<?php\n"
        + "$input = json_decode(base64_decode('" + payload + "'),true);\n"
        + "chdir('/home/web/vm-23f9aff9.na4u.ru/www');\n"
        + "require_once '/home/web/vm-23f9aff9.na4u.ru/www/wa-config/SystemConfig.class.php';\n"
        + "waSystem::getInstance(null,new SystemConfig());wa('shop');\n"
        + code + "\n?>\nPHP\n"
        "chown web:web /tmp/riva_uch1998_once.php\n"
        "su -s /bin/bash web -c 'php -d display_errors=0 /tmp/riva_uch1998_once.php'\n"
        "rc=$?; rm -f /tmp/riva_uch1998_once.php; exit $rc\n"
    )
    out = execute(remote)
    matches = [l for l in out.splitlines() if l.startswith("RIVA_CONTENT_RESULT=")]
    if len(matches) != 1:
        raise RuntimeError("Expected single guarded PHP response")
    return json.loads(matches[0].split("=", 1)[1])


PREFLIGHT = r"""
$m=new waModel();
$p=$m->query('SELECT id,name,type_id,description,summary,image_id FROM shop_product WHERE id=1489551')->fetch();
$skus=$m->query('SELECT id,sku,name,price,purchase_price FROM shop_product_skus WHERE product_id=1489551')->fetchAll();
if(!$p || (int)$p['type_id']!==136 || count($skus)!==1
    || (int)$skus[0]['id']!==31668689 || (string)$skus[0]['sku']!=='УЧ-00001998')
    {fwrite(STDERR,"Target product mismatch\n");exit(4);}
$s=$m->query("SELECT id,code FROM shop_megasuppliers_supplier WHERE id=16")->fetch();
if(!$s || strtoupper((string)$s['code'])!=='RIVA')
    {fwrite(STDERR,"Supplier identity changed\n");exit(4);}
$dir=wa()->getDataPath('plugins/megasuppliers/import-config',false,'shop',true);
$cfg=json_decode(file_get_contents($dir.'/16.json'),true);
if(!is_array($cfg) || strtolower((string)ifset($cfg['source']['format']))!=='yml'
    || (int)ifset($cfg['webasyst']['type_id'])!==136) {fwrite(STDERR,"Config mismatch\n");exit(5);}
$fc=(int)$m->query("SELECT COUNT(*) FROM shop_product_features WHERE product_id=1489551")->fetchField();
$stocks=$m->query("SELECT stock_id,count FROM shop_product_stocks WHERE sku_id=31668689 ORDER BY stock_id")->fetchAll();
$pr=array('price'=>ifset($skus[0]['price']),'purchase_price'=>ifset($skus[0]['purchase_price']));
$guard=hash('sha256',json_encode(array('price'=>$pr,'stocks'=>$stocks,'summary'=>$p['summary'],'image_id'=>$p['image_id']),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES));
if(strlen((string)$p['description'])>0 || $fc>0) {fwrite(STDERR,"Target no longer empty; no overwrite\n");exit(6);}
echo 'RIVA_CONTENT_RESULT='.json_encode(array(
  'source_location'=>(string)ifset($cfg['source']['location']),
  'supplier_id'=>16,'product_id'=>(int)$p['id'],'sku_id'=>(int)$skus[0]['id'],
  'previous_description_length'=>strlen((string)$p['description']),
  'previous_feature_count'=>$fc,'unchanged_guard'=>$guard
),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES).PHP_EOL;
"""

ENSURE = r"""
$m=new waModel();
$p=$m->query('SELECT id,type_id,description FROM shop_product WHERE id=1489551')->fetch();
if(!$p || (int)$p['type_id']!==136 || strlen((string)$p['description'])>0
   || (int)$m->query('SELECT COUNT(*) FROM shop_product_features WHERE product_id=1489551')->fetchField()>0)
    {fwrite(STDERR,"Target changed before feature creation\n");exit(7);}
$names=ifset($input['names'],array());
if(!is_array($names)||count($names)<5||count($names)>40){fwrite(STDERR,"Unexpected characteristic count\n");exit(8);}
$featureModel=new shopFeatureModel();
$typeModel=new shopTypeFeaturesModel();
$mapping=array();
foreach($names as $raw) {
  $name=trim((string)$raw);
  if($name===''||mb_strlen($name,'UTF-8')>90) {fwrite(STDERR,"Invalid name\n");exit(9);}
  $key=mb_strtolower($name,'UTF-8');
  $code='ms_s16_'.substr(hash('sha256',$key),0,16);
  $f=$featureModel->getByField('code',$code);
  if(!$f) {
    $fid=$featureModel->save(array(
      'code'=>$code,'name'=>$name,'type'=>shopFeatureModel::TYPE_VARCHAR,
      'selectable'=>0,'multiple'=>0,'status'=>'public','available_for_sku'=>0
    ));
    if(!$fid){fwrite(STDERR,"Could not create characteristic\n");exit(10);}
  } else {
    $fid=(int)$f['id'];
    if((string)ifset($f['status'])==='private') {
      $featureModel->updateById($fid,array('status'=>'public'));
    }
  }
  $typeModel->updateByFeature((int)$fid,array(136),false);
  $mapping[$name]=$code;
}
echo 'RIVA_CONTENT_RESULT='.json_encode(array('mapping'=>$mapping,'count'=>count($mapping)),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES).PHP_EOL;
"""

VERIFY = r"""
$m=new waModel();
$p=$m->query('SELECT id,description,summary,image_id FROM shop_product WHERE id=1489551')->fetch();
$sku=$m->query('SELECT id,sku,price,purchase_price FROM shop_product_skus WHERE id=31668689')->fetch();
$stocks=$m->query('SELECT stock_id,count FROM shop_product_stocks WHERE sku_id=31668689 ORDER BY stock_id')->fetchAll();
$pr=array('price'=>ifset($sku['price']),'purchase_price'=>ifset($sku['purchase_price']));
$guard=hash('sha256',json_encode(array('price'=>$pr,'stocks'=>$stocks,'summary'=>$p['summary'],'image_id'=>$p['image_id']),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES));
$f=(int)$m->query('SELECT COUNT(*) FROM shop_product_features WHERE product_id=1489551')->fetchField();
echo 'RIVA_CONTENT_RESULT='.json_encode(array(
 'description_length'=>strlen((string)$p['description']),
 'description_preview'=>mb_substr(strip_tags((string)$p['description']),0,200,'UTF-8'),
 'feature_count'=>$f,'guard_matches'=>$guard===(string)ifset($input['guard']),
 'sku'=>$sku['sku']
),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES).PHP_EOL;
"""


def main():
    assert os.environ.get("WEBASYST_API_TOKEN")
    pre = run_php(PREFLIGHT)
    if pre["product_id"] != int(PRODUCT_ID) or pre["sku_id"] != int(SKU_ID):
        raise RuntimeError("Preflight identity mismatch")
    loc = pre.get("source_location") or ""
    if not loc.startswith("https://riva.ru/api/"):
        raise RuntimeError("Unexpected Riva source URL")
    cfg = {
        "code": "RIVA", "name": "Riva",
        "source": {"format": "yml", "kind": "url", "location": loc},
        "identity": {"supplier_sku_field": "Код для сайта", "sku_prefix": "", "brand": "Riva"},
        "mapping": {"name": "name", "description": "description", "images": []},
        "rules": {"price_formulas": {}},
    }
    # The supplier URL is not printed or committed.
    data = fetch_source(cfg)
    rows = load_xml_yml(data)
    found = [row for row in rows if str(row.get("Код для сайта") or "").strip() == SKU]
    if len(found) != 1 or "Икспресс" not in (found[0].get("name") or ""):
        raise RuntimeError("Cannot unambiguously locate SKU in fresh Riva source")
    product = normalize(cfg, found)[0]
    chars = product.characteristics
    description = product.description
    if not product.description_generated or not (8 <= len(chars) <= 35):
        raise RuntimeError("Unexpected Riva product content")
    if not (150 <= len(description) < 5000):
        raise RuntimeError("Unexpected generated description length")
    print("RIVA_CONTENT_PREFLIGHT=" + json.dumps({
        "product_id": PRODUCT_ID, "sku": SKU, "feed_rows": len(rows),
        "feature_fields": len(chars), "description_length": len(description),
    }, ensure_ascii=False))
    created = run_php(ENSURE, {"names": list(chars.keys())})
    mapping = created.get("mapping") or {}
    if len(mapping) != len(chars):
        raise RuntimeError("Feature mapping incomplete")
    api = WebasystClient()
    fields = {mapping[k]: v for k, v in chars.items()}
    payload = {"description": description, "features": fields}
    if set(payload) != {"description", "features"}:
        raise RuntimeError("Unexpected write fields")
    result = api.call("shop.product.update", http_method="POST", params={"id": PRODUCT_ID}, data=payload)
    verified = run_php(VERIFY, {"guard": pre["unchanged_guard"]})
    if not verified.get("guard_matches") or verified.get("description_length", 0) < 150 or verified.get("feature_count", 0) < 8:
        raise RuntimeError("Product verification failed; inspect one-time job before retry")
    print("RIVA_CONTENT_REPAIR_SUCCESS=" + json.dumps({
        "product_id": PRODUCT_ID, "sku": verified["sku"],
        "description_length": verified["description_length"],
        "description_preview": verified["description_preview"],
        "feature_count": verified["feature_count"],
        "prices_stocks_images_unchanged": verified["guard_matches"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
