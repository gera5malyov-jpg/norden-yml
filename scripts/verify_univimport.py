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
    raise RuntimeError("Plugin factory could not load installed Universal Import")
