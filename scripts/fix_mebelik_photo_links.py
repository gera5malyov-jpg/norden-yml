#!/usr/bin/env python3
"""Repair YML Pro profile 28: correct HTTPS source + image URL; no other product fields."""
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute

ROOT_CA = Path("storefront/certs/mebelik-globalsign-root-r46.pem")
CERT_B64 = base64.b64encode(ROOT_CA.read_bytes()).decode("ascii")
REMOTE_PATCH = r'''set -euo pipefail
python3 - <<'REMOTE'
import os
import pathlib
import shutil
import subprocess
import tempfile
import base64
from datetime import datetime, timezone

root = pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
target = root / 'wa-apps/shop/plugins/univimport/lib/classes/shopUnivimportPluginUniversalPhotoSync.class.php'
certpath = root / 'wa-config/mebelik-globalsign-root-r46.pem'
certificate = base64.b64decode('__ROOT_CA_BASE64__')
if not (certificate.startswith(b'-----BEGIN CERTIFICATE-----') and certificate.rstrip().endswith(b'-----END CERTIFICATE-----')):
    raise RuntimeError('Untrusted certificate file format')
# A trusted public GlobalSign R46 root, not a bypass of TLS checks.
with tempfile.NamedTemporaryFile('wb', dir=str(certpath.parent), prefix='.mebelik-ca-', delete=False) as cf:
    cf.write(certificate)
    ctmp=cf.name
os.chmod(ctmp,0o644)
os.rename(ctmp,str(certpath))
s = target.read_text(encoding='utf-8')
marker = 'MBK_PROFILE_28_CERTIFIED_HTTPS'
if marker in s:
    print('MBK_PATCH='+str({'already_active':True, 'root_cert_ready':True}),flush=True)
else:
    source_needle = "$url=isset($c['url'])?trim((string)$c['url']):'';"
    source_replace = """$url=isset($c['url'])?trim((string)$c['url']):'';
        // MBK_PROFILE_28_CERTIFIED_HTTPS: source lives in YML Pro file manager.
        // Rebuild only the verified Mebelik host, avoiding its broken www TLS hostname.
        if ($url==='' && (int)$id===28 && strtolower(trim((string)$row['name']))==='mebelik') {
            $fileInfo=shopYmlFilesHelper::getFileInfo(28);
            if (is_array($fileInfo) && isset($fileInfo['upload_type'])
                && strtolower((string)$fileInfo['upload_type'])==='url' && !empty($fileInfo['url'])) {
                $sourceParts=parse_url(trim((string)$fileInfo['url']));
                if (is_array($sourceParts) && !empty($sourceParts['host'])
                    && in_array(strtolower($sourceParts['host']),array('mebelik.ru','www.mebelik.ru'),true)
                    && !empty($sourceParts['path']) && empty($sourceParts['user']) && empty($sourceParts['pass'])) {
                    $url='https://mebelik.ru'.$sourceParts['path'].
                         (isset($sourceParts['query'])?'?'.$sourceParts['query']:'');
                }
            }
        }"""
    images_needle = "$photos=self::xmlPictures($reader->readOuterXML());"
    images_replace = """$photos=self::xmlPictures($reader->readOuterXML());
            // Only profile 28: convert Mebelik-origin images to verified HTTPS no-www host.
            if ((int)$profileId===28) {
                foreach($photos as &$photoUrl) {
                    $photoUrl=preg_replace('~^https?://(www[.])?mebelik[.]ru(?=/|$)~i',
                        'https://mebelik.ru',$photoUrl);
                }
                unset($photoUrl);
            }"""
    feed_needle = "$ch=curl_init($url);"
    feed_replace = """$ch=curl_init($url);
        if (strtolower((string)parse_url($url,PHP_URL_HOST))==='mebelik.ru') {
            $ca='/home/web/vm-23f9aff9.na4u.ru/www/wa-config/mebelik-globalsign-root-r46.pem';
            if (!is_readable($ca)) throw new RuntimeException('Mebelik CA bundle missing');
            curl_setopt($ch,CURLOPT_CAINFO,$ca);
        }"""
    image_check_needle = "$ch=curl_init($match[1]);"
    image_check_replace = """$ch=curl_init($match[1]);
            if (strtolower((string)parse_url($match[1],PHP_URL_HOST))==='mebelik.ru') {
                $ca='/home/web/vm-23f9aff9.na4u.ru/www/wa-config/mebelik-globalsign-root-r46.pem';
                if (!is_readable($ca)) throw new RuntimeException('Mebelik CA bundle missing');
                curl_setopt($ch,CURLOPT_CAINFO,$ca);
            }"""
    updates = ((source_needle,source_replace),(images_needle,images_replace),
               (feed_needle,feed_replace),(image_check_needle,image_check_replace))
    for old,new in updates:
        if s.count(old)!=1: raise RuntimeError('PHP structure changed; guard failed: '+old[:60])
        s=s.replace(old,new,1)
    backups=root.parent/'storefront-backups'
    backups.mkdir(parents=True,exist_ok=True)
    backup=backups/('mebelik-https-photo-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.php')
    shutil.copy2(str(target),str(backup))
    os.chmod(str(backup),0o600)
    stat=target.stat()
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(target.parent),prefix='.mbk-',delete=False) as f:
        f.write(s);tmp=f.name
    os.chmod(tmp,stat.st_mode)
    os.chown(tmp,stat.st_uid,stat.st_gid)
    try:
        check=subprocess.run(['php','-l',tmp],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=25)
        if check.returncode:raise RuntimeError('PHP lint failed: '+check.stderr[:250])
        os.rename(tmp,str(target))
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    print('MBK_PATCH='+str({'patched':True,'backup':str(backup),'php_lint':'ok','root_cert_ready':True}),flush=True)
REMOTE
'''.replace('__ROOT_CA_BASE64__',CERT_B64)

REMOTE_APPLY = r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/mebelik-photo-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
require_once 'wa-apps/shop/plugins/univimport/lib/classes/shopUnivimportPluginUniversalPhotoSync.class.php';
$preview=shopUnivimportPluginUniversalPhotoSync::run(28,false);
echo 'MBK_PREVIEW='.json_encode($preview,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
if (!isset($preview['status']) || $preview['status']!=='preview'
    || (int)$preview['mapped']<900 || (int)$preview['matched']<850
    || (int)$preview['eligible']<800 || (int)$preview['insecure_images']>0
    || (int)$preview['ambiguous_mapping']>0 || (int)$preview['cross_profile_skipped']>0) {
    throw new RuntimeException('Mebelik photo preflight guard failed');
}
if ((int)$preview['changed']>0) {
    $applied=shopUnivimportPluginUniversalPhotoSync::run(28,true);
    echo 'MBK_APPLY='.json_encode($applied,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
    if (!isset($applied['status']) || $applied['status']!=='apply'
        || (int)$applied['updated']<1 || (int)$applied['concurrent_skipped']>0) {
        throw new RuntimeException('Mebelik photo apply did not complete safely');
    }
} else {
    echo 'MBK_APPLY={"status":"already_current","updated":0}'."\n";
}
$after=shopUnivimportPluginUniversalPhotoSync::run(28,false);
echo 'MBK_POST='.json_encode($after,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
if ($after['status']!=='preview' || (int)$after['changed']!==0 || (int)$after['insecure_images']!==0) {
    throw new RuntimeException('Mebelik photo post-check failed');
}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""

def main():
    out=execute(REMOTE_PATCH)
    for l in out.splitlines():
        if l.startswith('MBK_PATCH='): print(l,flush=True)
    if 'MBK_PATCH=' not in out: raise RuntimeError('Mebelik scoped patch not confirmed')
    result=execute(REMOTE_APPLY)
    for l in result.splitlines():
        if l.startswith(('MBK_PREVIEW=','MBK_APPLY=','MBK_POST=')):print(l,flush=True)
    if 'MBK_POST=' not in result: raise RuntimeError('Mebelik refresh not verified')
    print('MBK_FINISHED=success',flush=True)

if __name__=="__main__":
    main()
