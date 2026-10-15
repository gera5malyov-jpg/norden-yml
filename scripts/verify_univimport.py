#!/usr/bin/env python3
"""Read-only runtime and boot diagnostics for installed Shop-Script plugin."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "storefront"))
from stage52_disable_callback import execute

remote = r"""set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
cd "$ROOT"
test -f wa-apps/shop/plugins/univimport/lib/config/plugin.php
test -f wa-apps/shop/plugins/univimport/templates/actions/backend/Backend.html
test -f wa-config/apps/shop/plugins.php
echo 'UNIVIMPORT_FILES=present'
echo 'UNIVIMPORT_WASYSTEM_PLUGIN_SOURCE_BEGIN'
sed -n '1370,1408p' wa-system/waSystem.class.php | sed -E 's/^[[:space:]]+/ /'
echo 'UNIVIMPORT_WASYSTEM_PLUGIN_SOURCE_END'
echo 'UNIVIMPORT_AUTOLOAD_FILES'
find wa-cache -maxdepth 5 -iname '*plugin*' -type f 2>/dev/null | grep -E '/shop/|autoload' | head -15 || true
echo 'UNIVIMPORT_PLUGIN_FILES'
find wa-apps/shop/plugins/univimport/lib -maxdepth 2 -type f -printf '%P\\n' | head -15
echo 'UNIVIMPORT_OTHER_PLUGIN_EXAMPLES'
for id in ozonstocksync megasuppliers; do ls -l "wa-apps/shop/plugins/$id/lib/shop"*.plugin.php 2>/dev/null || true; done
cat >/tmp/verify-univimport-run.php <<'VERIFY_PHP'
<?php
error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
$plugins=include $root.'/wa-config/apps/shop/plugins.php';
$config=include $root.'/wa-apps/shop/plugins/univimport/lib/config/plugin.php';
echo 'UNIVIMPORT_REGISTRY='.(!empty($plugins['univimport']) ? 'enabled' : 'disabled').';version='.$config['version']."\n";
try {
 require_once $root.'/wa-config/SystemConfig.class.php';
 waSystem::getInstance(null,new SystemConfig());
 echo "UNIVIMPORT_BOOT=init_ok\n";
 wa('shop');
 echo "UNIVIMPORT_BOOT=shop_ok\n";
 $pluginFile=$root.'/wa-apps/shop/plugins/univimport/lib/shopUnivimport.plugin.php';
 echo "UNIVIMPORT_CLASS_FILE=".(is_file($pluginFile) ? 'yes' : 'no').';readable='.(is_readable($pluginFile)?'yes':'no')."\n";
 echo "UNIVIMPORT_CLASS_PRE=".(class_exists('shopUnivimportPlugin',false)?'yes':'no')."\n";
 echo "UNIVIMPORT_SHOP_BASE=".(class_exists('shopPlugin')?'yes':'no')."\n";
 try {
   require_once $pluginFile;
   echo "UNIVIMPORT_CLASS_MANUAL=".(class_exists('shopUnivimportPlugin',false)?'yes':'no')."\n";
 } catch (Throwable $err) { echo "UNIVIMPORT_CLASS_INCLUDE_ERROR=".preg_replace('/\\s+/', ' ', strip_tags($err->getMessage()))."\n"; }
 $plugin=wa('shop')->getPlugin('univimport');
 if (!$plugin) throw new RuntimeException('Plugin factory returned false');
 echo "UNIVIMPORT_RUNTIME=".get_class($plugin)."\n";
 $defaults=shopUnivimportPluginProfiles::defaults();
 if (!empty($defaults['allow_write'])) throw new RuntimeException('Write guard not disabled by default');
 echo "UNIVIMPORT_WRITE_GUARD=off\n";
} catch (Throwable $e) {
 $message=strip_tags((string)$e->getMessage());
 echo 'UNIVIMPORT_RUNTIME_ERROR='.substr(preg_replace('/\\s+/', ' ', $message),0,400)."\n";
}
VERIFY_PHP
chmod 644 /tmp/verify-univimport-run.php
su -s /bin/bash web -c 'php -d display_errors=0 /tmp/verify-univimport-run.php'
rm -f /tmp/verify-univimport-run.php
STATUS=$(curl -kLsS --max-time 30 -o /dev/null -w '%{http_code}' https://profikompany.ru/)
echo "UNIVIMPORT_FRONTEND=http_$STATUS"
"""
result = execute(remote)
for line in result.splitlines():
    if line.startswith("UNIVIMPORT_"):
        print(line, flush=True)
if "UNIVIMPORT_REGISTRY=enabled" not in result or "UNIVIMPORT_FRONTEND=http_200" not in result:
    raise RuntimeError("Installed plugin or storefront not verified")
if "UNIVIMPORT_RUNTIME=shopUnivimportPlugin" not in result:
    print("UNIVIMPORT_DIAG=runtime_incomplete", flush=True)
