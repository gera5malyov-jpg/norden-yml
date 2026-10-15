#!/usr/bin/env python3
"""Read-only runtime validation for the installed Shop-Script univimport."""
import json
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
su -s /bin/bash web -c 'php -d display_errors=1 -r '\''require_once "wa-config/SystemConfig.class.php"; waSystem::getInstance(null,new SystemConfig()); wa("shop"); $plugins=include "wa-config/apps/shop/plugins.php"; if(empty($plugins["univimport"])) throw new Exception("Not enabled"); $plugin=wa("shop")->getPlugin("univimport"); if(!$plugin) throw new Exception("Plugin not resolved"); if(get_class($plugin)!=="shopUnivimportPlugin") throw new Exception("Wrong plugin class"); $config=include "wa-apps/shop/plugins/univimport/lib/config/plugin.php"; if($config["version"]!=="0.2.0") throw new Exception("Wrong version"); $obj=shopUnivimportPluginProfiles::defaults(); if(!empty($obj["allow_write"])) throw new Exception("Writes enabled by default"); echo "UNIVIMPORT_RUNTIME=loaded;version=".$config["version"].";allow_write=0\n"; '\'''
STATUS=$(curl -kLsS --max-time 30 -o /dev/null -w '%{http_code}' https://profikompany.ru/)
test "$STATUS" = 200
echo "UNIVIMPORT_FRONTEND=http_$STATUS"
"""
result = execute(remote)
for line in result.splitlines():
    if line.startswith("UNIVIMPORT_"):
        print(line, flush=True)
if "UNIVIMPORT_RUNTIME=loaded" not in result or "UNIVIMPORT_FRONTEND=http_200" not in result:
    raise RuntimeError("Live plugin validation failed")
