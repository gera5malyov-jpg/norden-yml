<?php
/**
 * Safe automated SKU code repair for YML PRO profile 289 ("33кровати").
 * Rule is not guessed: all previously assigned SKU codes in the profile
 * equal 'AF-' . shop_product_skus.id.
 *
 * Never edits the YML PRO plugin, product identifiers, stock, prices or images.
 * Run from CLI, default dry-run. Applies only to empty SKU codes.
 */
if (PHP_SAPI !== 'cli') { http_response_code(403); exit(1); }
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
ini_set('display_errors','0');
set_time_limit(150);
$apply=in_array('--apply',isset($argv)?$argv:array(),true);
$root=dirname(__FILE__,7);
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$db=new waModel();
$lockDir=wa()->getDataPath('plugins/univimport/locks',false,'shop',true);
if(!is_dir($lockDir))waFiles::create($lockDir);
$lock=fopen($lockDir.'/sku-code-autofill-289.lock','c');
if(!$lock || !flock($lock,LOCK_EX|LOCK_NB)){
 echo 'SKU_AUTOFILL='.json_encode(array('status'=>'already_running')).PHP_EOL;
 exit(0);
}
try {
 $profile=$db->query('SELECT name FROM shop_yml_profiles WHERE id=289')->fetch();
 if(!$profile || trim((string)$profile['name'])!=='33кровати')throw new RuntimeException('Profile 289 identity mismatch');
 $all=$db->query("SELECT s.id sku_id,s.product_id,s.sku FROM shop_yml_products m " .
   "JOIN shop_product_skus s ON s.product_id=m.shop_id WHERE m.profile_id=289 ORDER BY s.id")->fetchAll();
 $observed=array();$candidates=array();$seen=array();$products=array();
 foreach($all as $row) {
  $id=(int)$row['sku_id'];$productId=(int)$row['product_id'];
  if($id<1 || isset($seen[$id]) || isset($products[$productId])){
   throw new RuntimeException('Ambiguous SKU or multiple SKUs per mapped product');
  }
  $seen[$id]=true;$products[$productId]=true;
  $code=isset($row['sku'])?(string)$row['sku']:'';
  if($code==='')$candidates[]=array('id'=>$id,'product_id'=>$productId,'old'=>$code,'new'=>'AF-'.$id);
  else $observed[]=array('id'=>$id,'code'=>$code);
 }
 if(count($observed)<100)throw new RuntimeException('Insufficient established supplier code history');
 foreach($observed as $x) {
  if($x['code']!=='AF-'.$x['id'])throw new RuntimeException('Supplier code convention changed; no updates');
 }
 if(count($candidates)>2000)throw new RuntimeException('Unexpected number of missing codes');
 if($candidates) {
   $escaped=array();
   foreach($candidates as $x)$escaped[]="'".$db->escape($x['new'])."'";
   $collisions=$db->query("SELECT COUNT(*) FROM shop_product_skus WHERE sku IN (".implode(',',$escaped).")")->fetchField();
   if((int)$collisions!==0)throw new RuntimeException('New code collides with an existing Webasyst SKU');
 }
 $result=array('status'=>$apply?'applied':'dry-run','profile_id'=>289,
    'existing_correct'=>count($observed),'missing_before'=>count($candidates),'updated'=>0,'skipped_concurrent'=>0);
 if($apply && $candidates) {
   $dir=wa()->getDataPath('plugins/univimport/rollback',false,'shop',true);
   if(!is_dir($dir))waFiles::create($dir);
   $path=$dir.'/sku-af-289-'.gmdate('Ymd-His').'-'.getmypid().'.json';
   $data=json_encode(array('profile_id'=>289,'created_at'=>gmdate('c'),'rows'=>$candidates),
      JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
   if(!$data || file_put_contents($path,$data,LOCK_EX)!==strlen($data))throw new RuntimeException('Cannot save rollback snapshot');
   @chmod($path,0600);
   foreach($candidates as $row) {
     $sql="UPDATE shop_product_skus SET sku='".$db->escape($row['new'])."' WHERE id=".(int)$row['id'].
      " AND product_id=".(int)$row['product_id']." AND (sku IS NULL OR sku='')";
     $db->exec($sql);
     $affected=(int)$db->query('SELECT ROW_COUNT()')->fetchField();
     if($affected===1)$result['updated']++;
     else $result['skipped_concurrent']++;
   }
   $missing=(int)$db->query("SELECT COUNT(*) FROM shop_yml_products m JOIN shop_product_skus s ON s.product_id=m.shop_id " .
      "WHERE m.profile_id=289 AND (s.sku='' OR s.sku IS NULL)")->fetchField();
   $result['missing_after']=$missing;
   $result['backup_saved']=is_file($path);
   if($missing>$result['skipped_concurrent'])throw new RuntimeException('Unfilled codes remain unexpectedly');
 }
 echo 'SKU_AUTOFILL='.json_encode($result,JSON_UNESCAPED_UNICODE).PHP_EOL;
} catch(Throwable $e) {
 echo 'SKU_AUTOFILL_ERROR='.json_encode(array('profile_id'=>289,'reason'=>substr($e->getMessage(),0,140)),JSON_UNESCAPED_UNICODE).PHP_EOL;
 exit(2);
} finally {
 flock($lock,LOCK_UN);fclose($lock);
}
