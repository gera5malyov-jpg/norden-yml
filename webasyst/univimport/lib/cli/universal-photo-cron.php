<?php
/**
 * Centralized YML PRO external-gallery cron.
 *
 * One supplier is processed once per six hours via hourly shard rotation.
 * The original YML PRO plugin and image files remain untouched.
 *
 * Usage (as the web user):
 *   php universal-photo-cron.php --preview --shard=0
 *   php universal-photo-cron.php --apply
 */
if (PHP_SAPI !== 'cli') {
    http_response_code(403);
    exit(1);
}
error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE & ~E_WARNING);
ini_set('display_errors','0');
set_time_limit(0);
$root=dirname(__FILE__,6);
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
require_once $root.'/wa-apps/shop/plugins/univimport/lib/classes/shopUnivimportPluginUniversalPhotoSync.class.php';

$args=isset($argv)?$argv:array();
$apply=in_array('--apply',$args,true);
$shard=(int)gmdate('G')%6;
foreach ($args as $arg) {
    if (preg_match('/^--shard=([0-5])$/',$arg,$parts))$shard=(int)$parts[1];
}
$lockDir=wa()->getDataPath('plugins/univimport/locks',false,'shop',true);
if(!is_dir($lockDir))waFiles::create($lockDir);
$lock=fopen($lockDir.'/universal-photo-cron.lock','c');
if(!$lock || !flock($lock,LOCK_EX|LOCK_NB)) {
    echo 'PHOTO_CRON='.json_encode(array('status'=>'overlapping_run_skipped')).PHP_EOL;
    exit(0);
}
$start=microtime(true);
$totals=array('profiles_checked'=>0,'updated'=>0,'unchanged'=>0,'skipped'=>0,'blocked'=>0,'errors'=>0);
try {
    $profiles=shopUnivimportPluginUniversalPhotoSync::profiles();
    foreach ($profiles as $entry) {
        $id=(int)$entry['id'];
        if ($id%6!==$shard || (int)$entry['linked']<1)continue;
        if (microtime(true)-$start>2700){
            echo 'PHOTO_CRON='.json_encode(array('status'=>'time_limit_reached','shard'=>$shard)).PHP_EOL;
            break;
        }
        try {
            $result=shopUnivimportPluginUniversalPhotoSync::run($id,$apply);
            $totals['profiles_checked']++;
            $totals['updated']+=isset($result['updated'])?(int)$result['updated']:0;
            $totals['unchanged']+=isset($result['unchanged'])?(int)$result['unchanged']:0;
            if (isset($result['status']) && $result['status']==='skipped')$totals['skipped']++;
            if (isset($result['status']) && $result['status']==='blocked')$totals['blocked']++;
            $safe=array('id'=>$id,'status'=>isset($result['status'])?$result['status']:'unknown',
               'matched'=>isset($result['matched'])?(int)$result['matched']:0,
               'changed'=>isset($result['changed'])?(int)$result['changed']:0,
               'updated'=>isset($result['updated'])?(int)$result['updated']:0,
               'reason'=>isset($result['reason'])?$result['reason']:'');
            echo 'PHOTO_PROFILE='.json_encode($safe,JSON_UNESCAPED_UNICODE).PHP_EOL;
        } catch(Throwable $e) {
            $totals['errors']++;
            $safe=array('id'=>$id,'status'=>'error','reason'=>substr($e->getMessage(),0,120));
            echo 'PHOTO_PROFILE='.json_encode($safe,JSON_UNESCAPED_UNICODE).PHP_EOL;
        }
    }
    $totals['status']='finished';
    $totals['shard']=$shard;
    $totals['mode']=$apply?'apply':'dry-run';
    $totals['elapsed_sec']=round(microtime(true)-$start,1);
    echo 'PHOTO_CRON='.json_encode($totals,JSON_UNESCAPED_UNICODE).PHP_EOL;
} finally {
    flock($lock,LOCK_UN);
    fclose($lock);
}
