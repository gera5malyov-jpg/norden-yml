#!/usr/bin/env python3
"""Scoped Mebelik: never put PDF/HTML/video into product photo extimg; existing YML Pro source unchanged."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute

PATCH=r"""set -euo pipefail
python3 - <<'REMOTE'
import os,pathlib,shutil,subprocess,tempfile
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
target=root/'wa-apps/shop/plugins/univimport/lib/classes/shopUnivimportPluginUniversalPhotoSync.class.php'
s=target.read_text(encoding='utf-8')
marker='MBK_PHOTO_EXTENSION_ALLOWLIST_20261019'
if marker in s:
 print('MBK_FILTER_PATCH=already_active',flush=True)
else:
 needle="""                unset($photoUrl);
            }"""
 if s.count(needle)!=1 or 'MBK_PROFILE_28_CERTIFIED_HTTPS' not in s:
  raise RuntimeError('PHP source guards failed - refusing modifications')
 repl="""                unset($photoUrl);
                // MBK_PHOTO_EXTENSION_ALLOWLIST_20261019: YML <picture> also includes PDFs,
                // HTML landing pages and MP4 instructions; they are not displayable photos.
                $photos=array_values(array_filter($photos,function($u) {
                    $parts=parse_url((string)$u);
                    $path=is_array($parts)&&isset($parts['path'])?$parts['path']:'';
                    $ext=strtolower(pathinfo((string)$path,PATHINFO_EXTENSION));
                    return in_array($ext,array('jpg','jpeg','png','webp','gif','avif'),true);
                }));
            }"""
 changed=s.replace(needle,repl,1)
 backupdir=root.parent/'storefront-backups'
 backupdir.mkdir(exist_ok=True,parents=True)
 bak=backupdir/('mebelik-filter-gallery-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.php')
 shutil.copy2(str(target),str(bak))
 os.chmod(str(bak),0o600)
 st=target.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(target.parent),prefix='.mebelik-filter-',delete=False) as f:
  f.write(changed)
  tmp=f.name
 os.chmod(tmp,st.st_mode);os.chown(tmp,st.st_uid,st.st_gid)
 try:
  test=subprocess.run(['php','-l',tmp],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=30)
  if test.returncode:raise RuntimeError('PHP lint failure '+test.stderr[:240])
  os.rename(tmp,str(target))
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
 print('MBK_FILTER_PATCH=patched_backup_created_and_php_linted',flush=True)
REMOTE
"""

APPLY=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/mbk-docfilter-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
require_once 'wa-apps/shop/plugins/univimport/lib/classes/shopUnivimportPluginUniversalPhotoSync.class.php';
$preview=shopUnivimportPluginUniversalPhotoSync::run(28,false);
echo 'MBK_FILTER_PREVIEW='.json_encode($preview,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
if (!isset($preview['status']) || $preview['status']!=='preview' 
  || (int)$preview['mapped']<1100 || (int)$preview['matched']<800 || (int)$preview['eligible']<800
  || (int)$preview['cross_profile_skipped']>0 || (int)$preview['ambiguous_mapping']>0
  || (int)$preview['insecure_images']>0 || (int)$preview['changed']<20) {
  throw new RuntimeException('Preflight mismatch; no gallery changes made');
}
$applied=shopUnivimportPluginUniversalPhotoSync::run(28,true);
echo 'MBK_FILTER_APPLY='.json_encode($applied,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
if ($applied['status']!=='apply' || (int)$applied['updated']<20 || empty($applied['backup_created'])
 || (int)$applied['concurrent_skipped']>0) throw new RuntimeException('Apply not confirmed');
$after=shopUnivimportPluginUniversalPhotoSync::run(28,false);
echo 'MBK_FILTER_POST='.json_encode($after,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
if ($after['status']!=='preview' || (int)$after['changed']!==0)
 throw new RuntimeException('Post-apply current YML gallery check failed');
$db=new waModel();
$rows=$db->query('SELECT p.id,p.summary FROM shop_yml_products m JOIN shop_product p ON m.shop_id=p.id WHERE m.profile_id=28 AND p.type_id=31')->fetchAll();
$pdf=0;$other=0;$photo=0;$ids=array();
foreach($rows as $r){
 preg_match_all('~\[extimg\]([\s\S]*?)\[/extimg\]~iu',(string)$r['summary'],$blocks);
 foreach($blocks[1] as $block){
  preg_match_all('~https?://[^\s<>"\'\[\]]+~iu',$block,$matches);
  foreach($matches[0] as $u){
   $ext=strtolower(pathinfo((string)parse_url($u,PHP_URL_PATH),PATHINFO_EXTENSION));
   if($ext==='pdf'){$pdf++;$ids[]=(int)$r['id'];}
   elseif(in_array($ext,array('htm','html','mp4','doc','docx','xls','xlsx','zip','xml'),true)){$other++;$ids[]=(int)$r['id'];}
   else $photo++;
  }
 }
}
$summary=array('mapped'=>count($rows),'photo_links'=>$photo,'pdf_gallery_links'=>$pdf,'other_nonphoto_gallery_links'=>$other,'sample_bad_ids'=>array_slice(array_values(array_unique($ids)),0,15));
echo 'MBK_FILTER_DB_CHECK='.json_encode($summary,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
if($pdf>0||$other>0)throw new RuntimeException('Some old Mebelik galleries still contain non-image files; need separate cleanup');
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""

def main():
 out=execute(PATCH)
 if 'MBK_FILTER_PATCH=' not in out:raise RuntimeError('Patch not verified')
 for l in out.splitlines():
  if l.startswith('MBK_FILTER_PATCH='):print(l,flush=True)
 out=execute(APPLY)
 for l in out.splitlines():
  if l.startswith(('MBK_FILTER_PREVIEW=','MBK_FILTER_APPLY=','MBK_FILTER_POST=','MBK_FILTER_DB_CHECK=')): print(l,flush=True)
 if 'MBK_FILTER_DB_CHECK=' not in out:raise RuntimeError('Database audit missing')
 print('MBK_FILTER_FINISHED=success',flush=True)

if __name__=='__main__':main()
