#!/usr/bin/env python3
"""Safely deduplicate two verified cross-file sitemap URL collisions; no catalog writes."""
import json
from stage52_disable_callback import execute

REMOTE = r"""set -eu
python3 - <<'PY'
from __future__ import print_function
import concurrent.futures
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

root = pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
target = root / 'wa-apps/shop/lib/config/shopSitemapConfig.class.php'
base = 'https://profikompany.ru/'
known = (
    'https://profikompany.ru/podstole/',
    'https://profikompany.ru/СФ-531201-Стол-левый-2200x1996x840-Дуб-Чарльстон-Бетон-Чикаго/',
)
prefix = '{http://www.sitemaps.org/schemas/sitemap/0.9}'
def digest(data):
    return hashlib.sha256(data).hexdigest()

def snapshot():
    index = subprocess.check_output(['curl','-fLsS','--compressed','--max-time','30',base+'sitemap.xml'], timeout=34)
    rootxml = ET.fromstring(index)
    parts = [e.text for e in rootxml.findall('.//'+prefix+'sitemap/'+prefix+'loc')]
    if len(parts) != 13 or len(set(parts)) != 13:
        raise RuntimeError('Unexpected sitemap index count/entries; do not edit')
    def fetch(url):
        data = subprocess.check_output(['curl','-fLsS','--compressed','--max-time','35',url], timeout=40)
        xm = ET.fromstring(data)
        return [e.text for e in xm.findall('.//'+prefix+'url/'+prefix+'loc') if e.text]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        groups = list(pool.map(fetch, parts))
    flat = [url for group in groups for url in group]
    seen = {}
    for url in flat:
        seen[url] = seen.get(url, 0) + 1
    duplicates = dict((u,n) for u,n in seen.items() if n > 1)
    return {'count':len(flat),'unique':set(flat),'dupes':duplicates,'pages':len(parts)}

old = target.read_bytes()
src = old.decode('utf-8')
start = """        // Scoped duplicate emission guard; preserve catalog and pagination.
        $profikompany_seen_product_urls = array();"""
start_new = """        // Scoped duplicate emission guard; preserve catalog and pagination.
        $profikompany_seen_product_urls = array();
        // Verified same-slug collisions across separate XML sitemap files.
        // Never modify product records: select one published ID for each URL.
        $profikompany_known_collision_slugs = array(
            'podstole' => true,
            'СФ-531201-Стол-левый-2200x1996x840-Дуб-Чарльстон-Бетон-Чикаго' => true,
        );
        $profikompany_canonical_product_by_slug = array();"""
existing = """                    $profikompany_output_url = $url;
                    $profikompany_scoped_url = strtolower((string) parse_url($profikompany_output_url, PHP_URL_HOST)) === 'profikompany.ru';
                    if (!$profikompany_scoped_url || !isset($profikompany_seen_product_urls[$profikompany_output_url])) {"""
replacement = """                    $profikompany_output_url = $url;
                    $profikompany_scoped_url = strtolower((string) parse_url($profikompany_output_url, PHP_URL_HOST)) === 'profikompany.ru';
                    $profikompany_crossfile_duplicate = false;
                    if ($profikompany_scoped_url && isset($profikompany_known_collision_slugs[$p['url']])) {
                        if (!isset($profikompany_canonical_product_by_slug[$p['url']])) {
                            $first_published_id = $product_model->query(
                                'SELECT MIN(id) FROM shop_product WHERE status = 1 AND url = s:slug',
                                array('slug' => $p['url'])
                            )->fetchField();
                            if (!$first_published_id) {
                                throw new RuntimeException('Missing canonical sitemap product for known slug');
                            }
                            $profikompany_canonical_product_by_slug[$p['url']] = (int) $first_published_id;
                        }
                        $profikompany_crossfile_duplicate = (int) $p['id'] !== $profikompany_canonical_product_by_slug[$p['url']];
                    }
                    if (!$profikompany_crossfile_duplicate && (!$profikompany_scoped_url || !isset($profikompany_seen_product_urls[$profikompany_output_url]))) {"""
if src.count(start) != 1 or src.count(existing) != 1 or 'profikompany_crossfile_duplicate' in src:
    raise RuntimeError('Generator already patched or changed unexpectedly - no modifications')
print('SITEMAP_PREFLIGHT='+json.dumps({'code_matched':True,'file_sha256':digest(old),'known_urls':len(known)}),flush=True)
before = snapshot()
if before['dupes'] != dict((u,2) for u in known):
    raise RuntimeError('Live sitemap no longer has exactly the two expected collisions: '+json.dumps(before['dupes'],ensure_ascii=False)[:350])
print('SITEMAP_BEFORE='+json.dumps({'urls':before['count'],'unique':len(before['unique']),'duplicates':before['dupes']},ensure_ascii=False),flush=True)
new = src.replace(start,start_new,1).replace(existing,replacement,1).encode('utf-8')
folder = root.parent / 'storefront-backups' / ('sitemap-crossfile-20261018-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700,parents=True)
backup = folder / 'shopSitemapConfig.class.php'
shutil.copy2(str(target),str(backup))
os.chmod(str(backup),0o600)
if digest(backup.read_bytes()) != digest(old) or digest(target.read_bytes()) != digest(old):
    raise RuntimeError('Backup verification or concurrent source change failed')
print('SITEMAP_BACKUP='+json.dumps({'file':str(backup),'sha256':digest(old)},ensure_ascii=False),flush=True)
stat = target.stat()
tmp = None
changed = False
try:
    with tempfile.NamedTemporaryFile('wb',dir=str(target.parent),prefix='.sitemap-safe-',delete=False) as f:
        f.write(new)
        tmp = f.name
    os.chmod(tmp,stat.st_mode & 0o777)
    os.chown(tmp,stat.st_uid,stat.st_gid)
    check = subprocess.run(['php','-l',tmp],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=25)
    if check.returncode != 0:
        raise RuntimeError('PHP syntax check failed: '+check.stdout[-300:]+check.stderr[-300:])
    if digest(target.read_bytes()) != digest(old):
        raise RuntimeError('Live generator changed concurrently; abort')
    os.replace(tmp,str(target))
    tmp = None
    changed = True
    os.utime(str(target),None)
    time.sleep(3)
    after = snapshot()
    if after['dupes'] or after['unique'] != before['unique'] or after['count'] != before['count']-2:
        raise RuntimeError('Postcheck mismatch: '+json.dumps({
          'remaining_dupes':after['dupes'],'count':after['count'],'expected_count':before['count']-2,
          'missing_unique':len(before['unique']-after['unique']),
          'new_unique':len(after['unique']-before['unique'])
        },ensure_ascii=False)[:450])
    if digest(target.read_bytes()) != digest(new):
        raise RuntimeError('Generator changed during acceptance checks')
    print('SITEMAP_DEPLOY='+json.dumps({
      'ok':True,'backup':str(backup),'file':str(target.relative_to(root)),
      'sitemaps':after['pages'],'urls':after['count'],'unique':len(after['unique']),
      'duplicate_urls':0,'same_unique_url_set':True,'products_prices_stocks_images_untouched':True
    },ensure_ascii=False),flush=True)
except Exception as e:
    if changed and digest(target.read_bytes()) == digest(new):
        shutil.copy2(str(backup),str(target))
        os.utime(str(target),None)
        print('SITEMAP_ROLLBACK='+json.dumps({'restored':True,'reason':str(e)[:400]},ensure_ascii=False),flush=True)
    elif changed:
        print('SITEMAP_ROLLBACK='+json.dumps({'restored':False,'reason':'Concurrent changes; review backup','backup':str(backup)},ensure_ascii=False),flush=True)
    if tmp and os.path.isfile(tmp):
        os.unlink(tmp)
    raise
PY
"""

def main():
    output = execute(REMOTE)
    for line in output.splitlines():
        if line.startswith(('SITEMAP_PREFLIGHT=','SITEMAP_BEFORE=','SITEMAP_BACKUP=','SITEMAP_DEPLOY=','SITEMAP_ROLLBACK=')):
            print(line,flush=True)
    if 'SITEMAP_DEPLOY=' not in output:
        raise RuntimeError('Live sitemap patch is not verified')

if __name__ == '__main__':
    main()
