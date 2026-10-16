<?php
/**
 * Repair only 4sis products with empty Webasyst summaries from the already
 * cached YML Pro profile #19. No external network access, no media downloads.
 */
set_time_limit(120);
if (PHP_SAPI!=='cli' || count($argv)!==2 || !in_array($argv[1],array('audit','apply'),true)) {
    fwrite(STDERR,"EXPECTED audit|apply\n");exit(2);
}
$mode=$argv[1];
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$model=new waModel();
$profileName=$model->query("SELECT name FROM shop_yml_profiles WHERE id=19")->fetchField();
if((string)$profileName!=='4sis')throw new RuntimeException('Unknown supplier profile');
$profile=new shopYmlProfile(19);
$filepath=$profile->getFile(false,false)->getFile();
if(!is_string($filepath)||!is_file($filepath)||filesize($filepath)<1000000) {
    throw new RuntimeException('4sis cached YML source unavailable');
}
$products=$model->query(
  "SELECT id,name,type_id,yml_id,yml_profile_id,summary,sku_id,price,compare_price,count FROM shop_product WHERE type_id=11 AND yml_profile_id=19 AND (summary='' OR summary IS NULL)"
)->fetchAll();
if(count($products)<50 || count($products)>85)throw new RuntimeException('Unexpected 4sis blank product count '.count($products));
$byCode=array();$bad=array();
foreach($products as $p) {
    $id=trim((string)$p['yml_id']);
    if($id==='' || isset($byCode[$id])){$bad[]=$p['id'];continue;}
    $byCode[$id]=$p;
}
if($bad)throw new RuntimeException('Duplicate or missing 4sis YML external IDs');
$rows=array();$seenCodes=array();$badOffers=array();
$reader=new XMLReader();
if(!$reader->open($filepath,null,LIBXML_NONET))throw new RuntimeException('Cannot open cached 4sis YML');
while($reader->read()) {
    if($reader->nodeType!==XMLReader::ELEMENT || $reader->localName!=='offer')continue;
    $code=(string)$reader->getAttribute('id');
    if(!isset($byCode[$code]))continue;
    if(isset($seenCodes[$code])){$badOffers[$code]=1;continue;}
    $seenCodes[$code]=1;
    $raw=$reader->readOuterXML();
    $offer=@simplexml_load_string($raw,'SimpleXMLElement',LIBXML_NONET|LIBXML_NOCDATA);
    if(!$offer)continue;
    $urls=array();$unique=array();
    foreach($offer->picture as $picture) {
        $url=trim((string)$picture);
        if(strlen($url)>1600 || !filter_var($url,FILTER_VALIDATE_URL))continue;
        $host=parse_url($url,PHP_URL_HOST);$scheme=parse_url($url,PHP_URL_SCHEME);
        if(!$host || !in_array($scheme,array('http','https'),true) || isset($unique[$url]))continue;
        $unique[$url]=true;$urls[]=$url;
    }
    if(!$urls)continue;
    $lines=array();
    foreach($urls as $url)$lines[]="[extimg]\n".$url."\n[/extimg]";
    $p=$byCode[$code];
    $rows[]=array('id'=>(int)$p['id'],'code'=>$code,'old_summary'=>(string)$p['summary'],
      'new_summary'=>implode("\n",$lines),'photo_count'=>count($urls),
      'price'=>(string)$p['price'],'compare_price'=>(string)$p['compare_price'],
      'purchase_price'=>(string)$p['purchase_price'],'stock'=>(string)$p['count'],
      'sku_id'=>(int)$p['sku_id']);
}
$reader->close();
$unmatched=array_diff(array_keys($byCode),array_keys($seenCodes));
echo "FOURSIS_RESTORE_PLAN ".json_encode(array(
  'mode'=>$mode,'missing_before'=>count($products),'recoverable'=>count($rows),
  'unmatched_external_ids'=>array_values($unmatched),
  'duplicate_source_codes'=>array_keys($badOffers),
  'total_external_photos'=>array_sum(array_column($rows,'photo_count'))
),JSON_UNESCAPED_UNICODE).PHP_EOL;
if($badOffers || count($rows)<55 || count($rows)>80)throw new RuntimeException('Source ambiguity or insufficient picture coverage');
if($mode==='audit')exit(0);
$dir=wa()->getDataPath('external-image-link-backups',false,'shop',true);
if(!is_dir($dir) && !mkdir($dir,0700,true))throw new RuntimeException('Cannot create image backup directory');
$backup=$dir.'/4sis-before-'.gmdate('Ymd-His').'-'.getmypid().'.json';
$blob=json_encode(array('provider'=>'4sis','profile'=>19,'date_utc'=>gmdate('c'),'updates'=>$rows),
 JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
if(!$blob||file_put_contents($backup,$blob,LOCK_EX)===false||filesize($backup)<2000)
 throw new RuntimeException('Backup failed; no modifications');
chmod($backup,0600);
echo "FOURSIS_BACKUP_OK ".json_encode(array('filename'=>basename($backup),'bytes'=>filesize($backup))).PHP_EOL;
$modelProduct=new shopProductModel();
$changed=0;$failed=array();
foreach($rows as $row) {
  $id=$row['id'];
  $before=$model->query(
    "SELECT id,yml_id,type_id,yml_profile_id,summary,sku_id,price,compare_price,count FROM shop_product WHERE id=i:id",
    array('id'=>$id))->fetch();
  if(!$before || (int)$before['type_id']!==11 || (int)$before['yml_profile_id']!==19
     || (string)$before['yml_id']!==$row['code']
     || trim((string)$before['summary'])!==''
     || (int)$before['sku_id']!==$row['sku_id']
     || (string)$before['price']!==$row['price']
     || (string)$before['compare_price']!==$row['compare_price']
     || (string)$before['count']!==$row['stock']) {
      $failed[]=array('id'=>$id,'reason'=>'concurrent_change');continue;
  }
  try {
    $modelProduct->updateById($id,array('summary'=>$row['new_summary']));
    $after=$model->query(
      "SELECT summary,type_id,yml_id,sku_id,price,compare_price,count FROM shop_product WHERE id=i:id",
      array('id'=>$id))->fetch();
    if(!$after || (string)$after['summary']!==$row['new_summary']
      || (int)$after['type_id']!==11 || (string)$after['yml_id']!==$row['code']
      || (int)$after['sku_id']!==$row['sku_id']
      || (string)$after['price']!==$row['price']
      || (string)$after['compare_price']!==$row['compare_price']
      || (string)$after['count']!==$row['stock']) {
        $failed[]=array('id'=>$id,'reason'=>'verification_mismatch');continue;
    }
    $changed++;
  } catch(Throwable $e) {
    $failed[]=array('id'=>$id,'reason'=>get_class($e));
  }
}
echo "FOURSIS_RESTORE_RESULT ".json_encode(array('updated'=>$changed,'failures'=>$failed,'missing_remaining'=>
  (int)$model->query("SELECT COUNT(*) FROM shop_product WHERE type_id=11 AND yml_profile_id=19 AND (summary='' OR summary IS NULL)")->fetchField()
),JSON_UNESCAPED_UNICODE).PHP_EOL;
if($failed)exit(5);
