#!/usr/bin/env python3
"""Scoped update: allow HTTP image URLs from existing YML Pro profile 8 only.

Does not alter feeds, source profiles, pricing, stock, product ownership or images.
Retains URL parsing checks, existing mapping gates, sample reachability test, backups,
product-summary concurrency checks and all other supplier HTTPS restrictions.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "storefront"))
from stage52_disable_callback import execute

PATCH = r"""set -euo pipefail
python3 - <<'REMOTE'
import os
import pathlib
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

root = pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
target = root / 'wa-apps/shop/plugins/univimport/lib/classes/shopUnivimportPluginUniversalPhotoSync.class.php'
s = target.read_text(encoding='utf-8')
old_gate = 'if($notSecure){$insecure++;continue;}'
new_gate = 'if($notSecure && (int)$profileId!==8){$insecure++;continue;}'
start = s.find('private static function testPhotos(')
end = s.find('public static function run(', start)
if start == -1 or end == -1: raise RuntimeError('testPhotos guard changed; no patch')
piece = s[start:end]
old_photo_pattern = '(https://'
new_photo_pattern = '(https?://'
if old_gate in s and old_photo_pattern in piece:
    if s.count(old_gate) != 1 or piece.count(old_photo_pattern) != 1:
        raise RuntimeError('Unexpected validation code; no patch')
    after = s[:start] + piece.replace(old_photo_pattern, new_photo_pattern, 1) + s[end:]
    after = after.replace(old_gate, new_gate, 1)
    base = root.parent / 'storefront-backups'
    base.mkdir(exist_ok=True, parents=True)
    backup = base / ('etazherka-photo-policy-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.php')
    shutil.copy2(str(target), str(backup))
    os.chmod(str(backup), 0o600)
    stat = target.stat()
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=str(target.parent), prefix='.etazherka-', delete=False) as tmp:
        tmp.write(after)
        tmpname = tmp.name
    os.chmod(tmpname, stat.st_mode)
    os.chown(tmpname, stat.st_uid, stat.st_gid)
    try:
        check = subprocess.run(['php', '-l', tmpname], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=20)
        if check.returncode: raise RuntimeError('PHP lint failed: ' + check.stderr[:250])
        os.rename(tmpname, str(target))
    finally:
        if os.path.exists(tmpname): os.unlink(tmpname)
    print('STAGE52_ETAZHERKA_PATCH=' + str({'patched':True,'backup':str(backup),'php_lint':'ok'}),flush=True)
elif new_gate in s and new_photo_pattern in piece:
    print('STAGE52_ETAZHERKA_PATCH=' + str({'patched':False,'already_active':True}),flush=True)
else:
    raise RuntimeError('Existing policy differs from preflight; no patch')
REMOTE
"""

RUN = r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/etazherka-photo-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig()); wa('shop');
require_once 'wa-apps/shop/plugins/univimport/lib/classes/shopUnivimportPluginUniversalPhotoSync.class.php';
$preview=shopUnivimportPluginUniversalPhotoSync::run(8,false);
echo 'STAGE52_ETAZHERKA_PREVIEW='.json_encode($preview,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
if ((int)$preview['matched']<1700 || (int)$preview['eligible']<1700
    || (int)$preview['insecure_images']!==0 || $preview['status']!=='preview') {
    throw new RuntimeException('Preflight mapping/safety mismatch, refusing apply');
}
if ((int)$preview['changed']>0) {
    $result=shopUnivimportPluginUniversalPhotoSync::run(8,true);
    echo 'STAGE52_ETAZHERKA_APPLY='.json_encode($result,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
    if ($result['status']!=='apply' || (int)$result['updated']<=0) {
        throw new RuntimeException('Apply did not complete; see result');
    }
} else {
    echo 'STAGE52_ETAZHERKA_APPLY='.json_encode(array('status'=>'already_current','updated'=>0))."\n";
}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""

def main():
    output = execute(PATCH)
    if 'STAGE52_ETAZHERKA_PATCH=' not in output:
        raise RuntimeError('Could not verify server PHP change')
    output = execute(RUN)
    for line in output.splitlines():
        if line.startswith(('STAGE52_ETAZHERKA_PREVIEW=', 'STAGE52_ETAZHERKA_APPLY=')):
            print(line, flush=True)
    if 'STAGE52_ETAZHERKA_APPLY=' not in output:
        raise RuntimeError('Missing apply result')
    result = json.loads(output.split('STAGE52_ETAZHERKA_APPLY=', 1)[1].splitlines()[0])
    if result.get('status') not in ('apply','already_current'):
        raise RuntimeError('Etazherka update not confirmed')
    print('STAGE52_ETAZHERKA_FINISHED=success',flush=True)

if __name__ == '__main__':
    main()
