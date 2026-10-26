<?php
// Read-only production diagnostic for Webasyst tip_ozon = "Информационный стенд".
error_reporting(E_ERROR);
$root = '/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null, new SystemConfig());
wa('shop');
$plugin = wa('shop')->getPlugin('ozonstocksync', true);
require_once $root.'/wa-apps/shop/plugins/ozonstocksync/lib/classes/shopOzonstocksyncPluginSync.class.php';
$model = new waModel();
$out = array('target' => 'Информационный стенд', 'plugin_version' => $plugin->getVersion());
$feature = $model->query("SELECT id FROM shop_feature WHERE code='tip_ozon' LIMIT 1")->fetchAssoc();
if (!$feature) throw new Exception('tip_ozon feature not found');
$feature_id = (int)$feature['id'];
$ids = array();
foreach (array('shop_feature_values_varchar', 'shop_feature_values_text') as $table) {
    try {
        $dict = $model->query('SELECT id FROM `'.$table.'` WHERE feature_id = i:id AND value = s:value',
            array('id'=>$feature_id, 'value'=>'Информационный стенд'))->fetchAll();
        foreach ($dict as $row) $ids[(int)$row['id']] = (int)$row['id'];
    } catch (Exception $e) {}
}
$columns = array();
foreach ($model->query('SHOW COLUMNS FROM shop_product_features')->fetchAll() as $col) $columns[$col['Field']] = true;
$pids = array();
if ($ids) {
    foreach (array('feature_value_id', 'value_id') as $col) {
        if (!isset($columns[$col])) continue;
        $rows=$model->query('SELECT DISTINCT product_id FROM shop_product_features WHERE feature_id=i:id AND `'.$col.'` IN (i:ids)', array('id'=>$feature_id, 'ids'=>array_values($ids)))->fetchAll();
        foreach ($rows as $row) $pids[(int)$row['product_id']] = (int)$row['product_id'];
    }
}
if (!$pids && isset($columns['value_varchar'])) {
    $rows=$model->query("SELECT DISTINCT product_id FROM shop_product_features WHERE feature_id=i:id AND value_varchar=s:value",array('id'=>$feature_id, 'value'=>'Информационный стенд'))->fetchAll();
    foreach ($rows as $row)$pids[(int)$row['product_id']]=(int)$row['product_id'];
}
$out['webasyst_product_count']=count($pids);
$out['products']=array();
$settings=array('tip_feature_code'=>'tip_ozon');
for ($i=1; $i<=10; $i++) {
    $suffix=$i===1?'':'_'.$i;
    foreach (array('account_name','client_id','api_key','mappings') as $field)$settings[$field.$suffix]=$plugin->getSettings($field.$suffix);
}
$sync=new shopOzonstocksyncPluginSync($settings);
$rc=new ReflectionMethod($sync,'parseAccounts');$rc->setAccessible(true);
$accounts=$rc->invoke($sync);
$out['accounts_checked']=count($accounts);
$lookup=new ReflectionMethod($sync,'getRemoteExistingOfferIds');$lookup->setAccessible(true);
$saved_lookup=new ReflectionMethod($sync,'getSavedOzonEvidence');$saved_lookup->setAccessible(true);
foreach($pids as $pid) {
    $pr=$model->query('SELECT id,name,url,status FROM shop_product WHERE id=i:id',array('id'=>$pid))->fetchAssoc();
    $skus=$model->query('SELECT id,sku,available,count FROM shop_product_skus WHERE product_id=i:id ORDER BY id',array('id'=>$pid))->fetchAll();
    $product=array('product_id'=>$pid,'name'=>$pr['name']??'','url'=>$pr['url']??'',
       'status'=>$pr['status']??null,'skus'=>array());
    foreach ($skus as $sku) {
        $sku_id=(int)$sku['id'];$value=trim((string)$sku['sku']);
        $one=array('sku_id'=>$sku_id,'webasyst_sku'=>$value,'available'=>$sku['available'],'webasyst_stock'=>$sku['count'],'account_checks'=>array());
        try {
            $saved=$model->query('SELECT id,ozon_product_id,ozon_offer_id,shop_sku_id FROM ozon_exported_product WHERE shop_sku_id=i:id ORDER BY id DESC LIMIT 10',array('id'=>$sku_id))->fetchAll();
            $one['export_table_rows']=$saved;
        } catch (Exception $e) {$one['export_table_error']='table or column unavailable';$saved=array();}
        $sets=$model->query('SELECT set_id FROM shop_set_products WHERE product_id=i:id',array('id'=>$pid))->fetchAll();
        $one['webasyst_sets']=array_column($sets,'set_id');
        foreach ($accounts as $account) {
            $candidate=array();
            if($value!=='')$candidate[$value]=$value;
            $pids_remote=array();
            if((int)$account['index']===1)foreach($saved as $ev){
                if(!empty($ev['ozon_offer_id']))$candidate[(string)$ev['ozon_offer_id']]=(string)$ev['ozon_offer_id'];
                if(!empty($ev['ozon_product_id']))$pids_remote[(string)$ev['ozon_product_id']]=(string)$ev['ozon_product_id'];
            }
            $remote=$lookup->invoke($sync,array_values($candidate),$account['client_id'],$account['api_key'],array_values($pids_remote));
            $found=array();
            if(!empty($remote['ok']))foreach($remote['existing'] as $rk=>$it){
                if(!is_array($it))continue;
                $key=(string)($it['offer_id']??'');
                if($key==='')continue;
                $found[$key]=array('offer_id'=>$key,'ozon_product_id'=>$it['product_id']??($it['id']??null),'ozon_sku'=>$it['sku']??null);
            }
            $mapping_sets=array();
            foreach($account['mappings'] as $mapping)$mapping_sets[]=(string)$mapping['set_id'];
            $one['account_checks'][]=array(
               'account_index'=>(int)$account['index'],'account_name'=>(string)$account['name'],
               'configured_sets'=>$mapping_sets,'product_in_export_set'=>(bool)count(array_intersect($mapping_sets,$one['webasyst_sets'])),
               'api_ok'=>(bool)($remote['ok']??false),'http_status'=>$remote['code']??null,
               'found_offers'=>array_values($found)
            );
        }
        $product['skus'][]=$one;
    }
    $out['products'][]=$product;
}
$out['price_api_checks'] = array();
$method = new ReflectionMethod($sync, 'sendToOzon');$method->setAccessible(true);
$offers = array('SAMS-291012', 'SAMS-290517');
foreach ($accounts as $account) {
    $response=$method->invoke($sync, 'https://api-seller.ozon.ru/v5/product/info/prices',
        array('cursor'=>'','filter'=>array('offer_id'=>$offers,'visibility'=>'ALL'),'limit'=>1000),
        $account['client_id'],$account['api_key']);
    $json=json_decode((string)($response['body']??''),true);
    $items=array();
    foreach (($json['items']??array()) as $item) {
        $pct=$item['commissions']['sales_percent_rfbs']??null;
        $items[]=array(
            'offer_id'=>$item['offer_id']??'',
            'product_id'=>$item['product_id']??null,
            'sales_percent_rfbs'=>$pct,
            'commission_fields'=>isset($item['commissions']) ? array_keys($item['commissions']) : array(),
            'price_present'=>isset($item['price']),
            'price_currency'=>$item['price']['currency_code']??null
        );
    }
    $out['price_api_checks'][]=array('account'=>$account['name'],'account_index'=>$account['index'],
      'http_ok'=>(bool)($response['ok']??false),'http_code'=>$response['code']??null,
      'returned_items'=>count($items),'items'=>$items,
      'result_top_keys'=>is_array($json)?array_keys($json):array());
}
echo json_encode($out, JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT|JSON_UNESCAPED_SLASHES).PHP_EOL;
