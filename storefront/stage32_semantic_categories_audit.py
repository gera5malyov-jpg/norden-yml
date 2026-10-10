#!/usr/bin/env python3
"""Stage 32: read-only category search-intent metadata audit.

Reads aggregate Shop-Script product, order, and feature health. No private records or writes.
"""
from __future__ import annotations
import json, os, subprocess, tempfile, urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE=r"""set -eu
p=$(mktemp /tmp/storefront-semantic-audit-XXXXXXXX.php)
trap 'rm -f "$p"' EXIT
cat >"$p" <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$m=new waModel();
function rows($m,$sql){try{return $m->query($sql)->fetchAll();}catch(Throwable $e){return array('error'=>get_class($e));}}
$summary=rows($m,"SELECT COUNT(*) total, SUM(status=1) visible, SUM(status=1 AND (meta_title IS NULL OR TRIM(meta_title)='')) visible_blank_title, SUM(status=1 AND (meta_description IS NULL OR TRIM(meta_description)='')) visible_blank_description, SUM(status=1 AND (description IS NULL OR TRIM(description)='')) visible_blank_body FROM shop_category");
$top=rows($m,"SELECT c.id,c.name,c.full_url,c.parent_id,c.count AS cached_count,
c.meta_title,c.meta_description,CHAR_LENGTH(c.description) AS body_length,
COUNT(p.id) AS active_main_count,
SUM(CASE WHEN p.id IS NOT NULL AND (p.count>0 OR p.count IS NULL) THEN 1 ELSE 0 END) AS active_instock_main_count
FROM shop_category c
LEFT JOIN shop_product p ON p.category_id=c.id AND p.status=1
WHERE c.status=1
GROUP BY c.id,c.name,c.full_url,c.parent_id,c.count,c.meta_title,c.meta_description,c.description
ORDER BY active_instock_main_count DESC LIMIT 65");
$near=rows($m,"SELECT name, COUNT(*) total, GROUP_CONCAT(id ORDER BY id SEPARATOR ',') AS ids FROM shop_category WHERE status=1 GROUP BY name HAVING COUNT(*)>1 ORDER BY total DESC LIMIT 25");
$plugins=rows($m,"SELECT id,name,type,plugin,status FROM shop_plugin WHERE LOWER(name) LIKE '%seo%' OR LOWER(name) LIKE '%поиск%' OR LOWER(plugin) LIKE '%seo%' ORDER BY status DESC LIMIT 35");
$out=array('categories_summary'=>$summary,'top_categories'=>$top,'duplicate_visible_category_names'=>$near,'seo_plugins'=>$plugins);
echo 'SEMANTIC_CATALOG_AUDIT='.json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
?>
PHP
chmod 644 "$p"
su -s /bin/bash web -c "php -d display_errors=1 -d log_errors=0 '$p'"
"""

def run():
  key=os.environ.get('NETANGELS_API_KEY','').strip()
  if not key:raise RuntimeError('Missing NETANGELS_API_KEY secret')
  token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
      urllib.parse.urlencode({'api_key':key}).encode(),
      {'Content-Type':'application/x-www-form-urlencoded'}).get('token')
  if not token:raise RuntimeError('NetAngels gateway token missing')
  headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
  with tempfile.TemporaryDirectory(prefix='storefront-stage32-semantic-') as td:
    ssh=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
    pub=Path(ssh+'.pub').read_text().strip()
    entry=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
        'POST',json.dumps({'key':pub,'name':'storefront-stage32-semantic-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    kid=entry.get('id')
    if not kid:raise RuntimeError('Unable to register temporary SSH key')
    try:
      r=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
            '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
            input=REMOTE,text=True,capture_output=True,timeout=160)
      for line in r.stdout.splitlines():
        if line.startswith('SEMANTIC_CATALOG_AUDIT='):print(line,flush=True)
      if r.returncode:
        raise RuntimeError('Storefront UX fix failed '+r.stdout[-1200:]+' '+r.stderr[-500:])
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
        print('temporary_ssh_key_removed=true',flush=True)
      except Exception:
        print('WARNING: temporary SSH cleanup needs review')
if __name__=='__main__':run()
