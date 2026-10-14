#!/usr/bin/env python3
"""One-time requested Red-Black profile SKU field update (profile only)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "storefront"))
from stage52_disable_callback import execute

REMOTE = r"""set -eu
cat >/tmp/megaprof_redblack_code_once.php <<'PHP'
<?php
$root = '/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null, new SystemConfig());
wa('shop');
$controller = $root.'/wa-apps/shop/plugins/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImportConfig.controller.php';
$source = file_get_contents($controller);
if (!is_string($source) || strpos($source, 'preserveUneditedConfig') === false
    || strpos($source, 'applyRedBlackDefaults') !== false) {
    fwrite(STDERR, "New editable profile controller not installed\n");
    exit(2);
}
$model = new waModel();
$rows = $model->query('SELECT id,code,name FROM shop_megasuppliers_supplier')->fetchAll();
$matches=array();
foreach ($rows as $supplier) {
    $v=preg_replace('/[^a-z0-9]/','',strtolower($supplier['code'].' '.$supplier['name']));
    if (strpos($v,'redblack') !== false) $matches[]=$supplier;
}
if (count($matches) !== 1) {
    fwrite(STDERR,'Ambiguous Red-Black supplier count '.count($matches)."\n");
    exit(3);
}
$id=(int)$matches[0]['id'];
$dir=wa()->getDataPath('plugins/megasuppliers/import-config',false,'shop',true);
$path=$dir.'/'.$id.'.json';
$original=file_get_contents($path);
$cfg=json_decode($original,true);
if (!is_array($cfg)) {
    fwrite(STDERR, "Invalid current supplier profile\n");
    exit(4);
}
$before=(string)ifset($cfg['identity']['supplier_sku_field'],'');
if ($before!=='article' && $before!=='code') {
    fwrite(STDERR,'Unexpected SKU source field: '.$before."\n");
    exit(5);
}
if ((string)ifset($cfg['identity']['sku_prefix'],'')!=='RED-00-'
    || (string)ifset($cfg['source']['format'],'')!=='yml'
    || (int)ifset($cfg['webasyst']['type_id'],0)!==150) {
    fwrite(STDERR,"Unexpected profile configuration, refused to edit\n");
    exit(6);
}
if ($before!=='code') {
    $backup=$path.'.bak-before-code-'.date('Ymd-His');
    if (!copy($path,$backup)) {
        fwrite(STDERR,"Backup failed\n");
        exit(7);
    }
    chmod($backup,0600);
    $cfg['identity']['supplier_sku_field']='code';
    $new=json_encode($cfg,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT);
    if (!is_string($new)) exit(8);
    if (waFiles::write($path,$new)===false) {
        fwrite(STDERR,"Profile write failed\n");
        exit(9);
    }
}
$verified=json_decode(file_get_contents($path),true);
if ((string)ifset($verified['identity']['supplier_sku_field'],'')!=='code'
    || (string)ifset($verified['identity']['sku_prefix'],'')!=='RED-00-') {
    fwrite(STDERR,"Red-Black profile roundtrip failed\n");
    exit(10);
}
echo 'STAGE52_RED_BLACK_CODE='.json_encode(array(
    'supplier_id'=>$id,
    'previous_field'=>$before,
    'saved_field'=>$verified['identity']['supplier_sku_field'],
    'sku_prefix'=>$verified['identity']['sku_prefix'],
    'type_id'=>(int)$verified['webasyst']['type_id'],
    'source_format'=>$verified['source']['format'],
    'only_profile_updated'=>true,
    'no_catalog_writes'=>true
),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES).PHP_EOL;
?>
PHP
chown web:web /tmp/megaprof_redblack_code_once.php
su -s /bin/bash web -c 'php -d display_errors=0 /tmp/megaprof_redblack_code_once.php'
rc=$?
rm -f /tmp/megaprof_redblack_code_once.php
exit "$rc"
"""


def main():
    output = execute(REMOTE)
    info = [x for x in output.splitlines() if x.startswith("STAGE52_RED_BLACK_CODE=")]
    if len(info) != 1:
        raise RuntimeError("Server did not confirm persisted Red-Black code mapping")
    result = json.loads(info[0].split("=", 1)[1])
    if result.get("saved_field") != "code" or not result.get("no_catalog_writes"):
        raise RuntimeError("Unexpected Red-Black profile readback")
    print("RED_BLACK_PROFILE_SAVE_CONFIRMED=" + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
