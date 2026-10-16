<?php
/**
 * Controlled Aletan YML Pro binding repair. No product/SKU price/stock modifications.
 * CLI: php al...apply.php one|all /tmp/plan.json
 */
set_time_limit(0);
ini_set('memory_limit','512M');
if (PHP_SAPI !== 'cli' || $argc !== 3 || !in_array($argv[1], array('one','all'), true)) {
    fwrite(STDERR, "INVALID_INVOCATION\n"); exit(2);
}
$mode = $argv[1];
$raw = @file_get_contents($argv[2]);
$plan = json_decode($raw ?: '', true);
if (!is_array($plan) || count($plan) < 600 || count($plan) > 900) {
    fwrite(STDERR, "INVALID_PLAN_SIZE\n"); exit(3);
}
$ROOT = '/home/web/vm-23f9aff9.na4u.ru/www';
chdir($ROOT);
require_once $ROOT.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null, new SystemConfig());
wa('shop');
$model = new waModel();
$profile = $model->query("SELECT id,name FROM shop_yml_profiles WHERE id=373")->fetch();
if (!$profile || (string)$profile['name'] !== 'Aletan') {
    throw new RuntimeException('Unexpected profile, blocked');
}
$seenOffer = array();
$seenShop = array();
$valid = array();
$errors = array();
$allIds = array();
foreach ($plan as $item) {
    if (!is_array($item)) throw new RuntimeException('Bad plan row');
    $pid = (int)($item['product_id'] ?? 0);
    $offer = trim((string)($item['offer_id'] ?? ''));
    if ($pid < 1 || !$offer || (int)($item['profile_id'] ?? 0) !== 373
        || isset($seenOffer[$offer]) || isset($seenShop[$pid])) {
        throw new RuntimeException('Invalid or duplicate identity in plan');
    }
    $seenOffer[$offer] = 1;
    $seenShop[$pid] = 1;
    $allIds[] = $pid;
    if ($mode === 'one' && $offer !== 'B102') continue;
    $p = $model->query(
        "SELECT id,name,type_id,yml_id,yml_profile_id FROM shop_product WHERE id=i:id",
        array('id' => $pid)
    )->fetch();
    if (!$p || (int)$p['type_id'] !== 202 || (int)$p['yml_profile_id'] !== 373
        || (string)$p['name'] !== (string)$item['name']) {
        $errors[] = "MISMATCH_PRODUCT_ID:$pid";
        continue;
    }
    $oldExt = trim((string)($p['yml_id'] ?? ''));
    if ($oldExt !== (string)$item['current_yml_id']) {
        $errors[] = "CHANGED_EXTERNAL_ID:$pid"; continue;
    }
    $stockSku = $model->query("SELECT sku FROM shop_product_skus WHERE product_id=i:id",
                              array('id'=>$pid))->fetchAll();
    if (count($stockSku) !== 1 || (string)$stockSku[0]['sku'] !== (string)$item['current_sku']) {
        $errors[] = "CHANGED_SKU:$pid"; continue;
    }
    $original = (string)$item['original_vendor_code'];
    if ($oldExt === '' && (string)$stockSku[0]['sku'] !== $original) {
        $errors[] = "NO_VENDOR_ID_EVIDENCE:$pid"; continue;
    }
    if ($oldExt !== '' && !in_array($oldExt,array($offer,$original),true)) {
        $errors[] = "WRONG_VENDOR_ID:$pid"; continue;
    }
    $alreadyForOffer = $model->query(
        "SELECT shop_id FROM shop_yml_products WHERE profile_id=373 AND yml_hash_id=s:hash",
        array('hash'=>md5($offer))
    )->fetchAll();
    if (count($alreadyForOffer) > 1
        || ($alreadyForOffer && (int)$alreadyForOffer[0]['shop_id'] !== $pid)) {
        $errors[] = "EXTERNAL_ID_TAKEN:$pid"; continue;
    }
    $existingMappings = $model->query(
        "SELECT yml_id FROM shop_yml_products WHERE profile_id=373 AND shop_id=i:id",
        array('id'=>$pid)
    )->fetchAll();
    if (count($existingMappings) > 1
        || ($existingMappings && (string)$existingMappings[0]['yml_id'] !== $offer)) {
        $errors[] = "PRODUCT_ALREADY_MAPPED_TO_OTHER_ID:$pid"; continue;
    }
    $item['_alreadyLinked'] = $alreadyForOffer && $existingMappings;
    $valid[] = $item;
}
if (!$valid) throw new RuntimeException('No safe candidates: '.json_encode($errors));
if ($mode === 'all' && (count($valid) < 750 || count($errors) > 15)) {
    throw new RuntimeException('Too many validation problems before apply: '
       .count($valid).' valid, '.count($errors).' conflicts');
}
echo 'PRECHECK '.json_encode(array('mode'=>$mode,'plan'=>count($plan),'valid'=>count($valid),
    'skipped'=>count($errors),'already_linked'=>count(array_filter($valid,function($x){return $x['_alreadyLinked'];}))),
    JSON_UNESCAPED_UNICODE).PHP_EOL;
if ($errors) echo "PRECHECK_ERRORS ".json_encode(array_slice($errors,0,15)).PHP_EOL;

// Store a recoverable snapshot of ALL Aletan mappings and the affected product IDs.
// No MySQL secrets and no customer/order data are included.
$ids = array_map('intval',$allIds);
$chunks = array_chunk($ids,150);
$oldProducts = array();
$oldFinder = array();
foreach($chunks as $chunk){
    $in=implode(',',$chunk);
    $oldProducts=array_merge($oldProducts,$model->query(
        "SELECT id,name,type_id,yml_id,yml_profile_id,edit_datetime FROM shop_product WHERE id IN ($in)"
    )->fetchAll());
    $oldFinder=array_merge($oldFinder,$model->query(
        "SELECT * FROM shop_yml_finder_cache WHERE product_id IN ($in)"
    )->fetchAll());
}
$oldMappings = $model->query(
    "SELECT * FROM shop_yml_products WHERE profile_id=373"
)->fetchAll();
$oldOfferIndex = $model->query(
    "SELECT * FROM shop_yml_offer_index WHERE profile_id=373"
)->fetchAll();
$backup = array(
    'created_utc'=>gmdate('c'),
    'mode'=>$mode,'profile'=>373,
    'shop_product'=>$oldProducts,
    'shop_yml_products'=>$oldMappings,
    'shop_yml_finder_cache'=>$oldFinder,
    'shop_yml_offer_index'=>$oldOfferIndex
);
$dir = wa()->getDataPath('aletan-ymlpro-binding-backups',false,'shop',true);
if (!is_dir($dir) && !@mkdir($dir,0700,true)) throw new RuntimeException('Cannot create backup directory');
$path = $dir.'/aletan-before-'.$mode.'-'.gmdate('Ymd-His').'-'.getmypid().'.json';
$json = json_encode($backup, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
if (!$json || file_put_contents($path,$json,LOCK_EX) === false) {
    throw new RuntimeException('Cannot save audit backup');
}
chmod($path,0600);
if (!is_file($path) || filesize($path)<1000) throw new RuntimeException('Invalid backup');
echo "BACKUP_OK ".json_encode(array('filename'=>basename($path),'mapping_rows'=>count($oldMappings),
    'product_rows'=>count($oldProducts),'bytes'=>filesize($path))).PHP_EOL;

$binding = new shopYmlBinding();
$created = 0;
$unchanged = 0;
$failed = array();
foreach ($valid as $item) {
    $pid = (int)$item['product_id'];
    $offer = (string)$item['offer_id'];
    if (!empty($item['_alreadyLinked'])) { $unchanged++; continue; }
    try {
        $binding->linkProduct(373,$pid,$offer,0);
        $check=$model->query(
            "SELECT shop_id FROM shop_yml_products WHERE profile_id=373 AND yml_hash_id=s:hash",
            array('hash'=>md5($offer))
        )->fetch();
        if (!$check || (int)$check['shop_id']!==$pid) throw new RuntimeException('not linked after save');
        $created++;
    } catch (Throwable $e) {
        $failed[] = array('offer'=>$offer,'product_id'=>$pid,'error'=>substr($e->getMessage(),0,120));
        if ($mode==='one' || count($failed)>10) break;
    }
}
echo "RESULT ".json_encode(array('mode'=>$mode,'created'=>$created,'existing'=>$unchanged,
  'failure_count'=>count($failed),'failures'=>array_slice($failed,0,10),
  'mapping_count_after'=>(int)$model->query(
    "SELECT COUNT(*) FROM shop_yml_products WHERE profile_id=373")->fetchField()
),JSON_UNESCAPED_UNICODE).PHP_EOL;
if ($failed) exit(5);
?>