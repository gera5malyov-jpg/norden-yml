#!/usr/bin/env python3
"""Refresh only Shop-Script's autoload index after manual plugin installation."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "storefront"))
from stage52_disable_callback import execute

remote = r"""set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
cd "$ROOT"
test -f wa-apps/shop/plugins/univimport/lib/shopUnivimport.plugin.php
test -f wa-apps/shop/plugins/yml/lib/config/plugin.php
TESTFILE="$(mktemp /tmp/univimport-autoload-XXXXXX.php)"
trap 'rm -f "$TESTFILE"' EXIT
cat >"$TESTFILE" <<'PHP'
<?php
error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
if (!method_exists('waAppConfig','clearAutoloadCache')) {
  echo "UNIVIMPORT_CACHE=unsupported\n";
  exit(2);
}
waAppConfig::clearAutoloadCache('shop');
echo "UNIVIMPORT_CACHE=shop_autoload_cleared\n";
PHP
chmod 644 "$TESTFILE"
su -s /bin/bash web -c "php -d display_errors=0 '$TESTFILE'"
cat >"$TESTFILE" <<'PHP'
<?php
error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$autoload=waAutoload::getInstance();
$names=array('shopUnivimportPlugin','shopUnivimportPluginProfiles','shopUnivimportPluginImporter','shopUnivimportPluginBackendAction','shopUnivimportPluginBackendSaveController','shopUnivimportPluginBackendRunController');
foreach($names as $name){
  $path=$autoload->get($name);
  echo "UNIVIMPORT_MAPPING=".$name.":".($path && is_file($path)?'ok':'missing')."\n";
  if (!$path || !is_file($path)) exit(5);
}
try {
  $plugin=wa('shop')->getPlugin('univimport');
  if (!$plugin || get_class($plugin)!=='shopUnivimportPlugin') throw new RuntimeException('Invalid plugin class');
  $defaults=shopUnivimportPluginProfiles::defaults();
  if (!empty($defaults['allow_write'])) throw new RuntimeException('Writes not disabled');
  echo "UNIVIMPORT_PLUGIN=loaded;write=disabled\n";
} catch (Throwable $e) {
  echo "UNIVIMPORT_PLUGIN_ERROR=".substr(strip_tags($e->getMessage()),0,200)."\n";
  exit(6);
}
PHP
chmod 644 "$TESTFILE"
su -s /bin/bash web -c "php -d display_errors=0 '$TESTFILE'"
echo "UNIVIMPORT_INSTALL_PRODUCTION=verified"
STATUS=$(curl -ksS -L --max-time 30 -o /dev/null -w '%{http_code}' https://profikompany.ru/)
test "$STATUS" = "200"
echo "UNIVIMPORT_FRONTEND=http_$STATUS"
"""
result = execute(remote)
for line in result.splitlines():
    if line.startswith("UNIVIMPORT_"):
        print(line, flush=True)
if "UNIVIMPORT_PLUGIN=loaded;write=disabled" not in result:
    raise RuntimeError("Installed plugin not yet usable")
if "UNIVIMPORT_FRONTEND=http_200" not in result:
    raise RuntimeError("Storefront probe failed")
