#!/usr/bin/env python3
"""Stage 10: safer product microdata + defer only gallery thumbnails.

Scope: 2 storefront theme files, no product/catalog/stock/order updates.
All original files backed up off web root; rollback on validation failure.
"""
from __future__ import annotations
import os, json, subprocess, tempfile, urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE=r"""set -eu
python3 - <<'PY'
import json,os,pathlib,shutil,subprocess,tempfile,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
theme=root/'wa-data/public/shop/themes/pureMegapolis42'
product=theme/'product.html'
gallery=theme/'product.gallery.html'
before={p:p.read_text(encoding='utf-8') for p in (product,gallery)}
a=before[product]
b=before[gallery]

# Important: external-image products have no native image markup; add metadata to their
# existing primary image WITHOUT lazy loading it (primary product image is LCP-critical).
article='<article class="product-page" itemscope itemtype="http://schema.org/Product">'
microdata='''<article class="product-page" itemscope itemtype="http://schema.org/Product">
{* Search snippet properties - visible product data only *}
<link itemprop="url" href="{$wa->shop->productUrl($product)|escape}">
{$_schema_sku = $product.skus[$product.sku_id]}
{if !empty($_schema_sku.sku)}
  <meta itemprop="sku" content="{$_schema_sku.sku|escape}">
{/if}'''
if a.count(article)!=1:raise RuntimeError('Product root did not match audited source')
a=a.replace(article,microdata,1)
primary='<img src="{$ext_images[0]|escape}" class="js-extimg-main-img" alt="{$product.name|escape}">'
primary_new='<img itemprop="image" src="{$ext_images[0]|escape}" class="js-extimg-main-img" alt="{$product.name|escape}">'
if a.count(primary)!=1:raise RuntimeError('External main photo does not match audited source')
a=a.replace(primary,primary_new,1)
thumb='''<img src="{$img_url|escape}"
                                     alt="{$product.name|escape}{if !$img_url@first} #{$img_url@iteration}{/if}">'''
thumb_new='''<img src="{$img_url|escape}" loading="lazy" decoding="async"
                                     alt="{$product.name|escape}{if !$img_url@first} #{$img_url@iteration}{/if}">'''
if a.count(thumb)!=1:raise RuntimeError('External thumbnails differ from audited source')
a=a.replace(thumb,thumb_new,1)

native="{$wa->shop->imgHtml($image, '54x54')}"
native_new="{$wa->shop->imgHtml($image, '54x54', ['loading' => 'lazy', 'decoding' => 'async'])}"
if b.count(native)!=1:raise RuntimeError('Native thumbnail helper does not match audited source')
b=b.replace(native,native_new,1)

changed={product:a,gallery:b}
backup_base=root.parent/'storefront-backups'
backup_base.mkdir(mode=0o700,parents=True,exist_ok=True)
backupdir=backup_base/('product-stage10-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
backupdir.mkdir(mode=0o700)
for p in changed:
  dest=backupdir/p.name
  shutil.copy2(str(p),str(dest))
  os.chmod(str(dest),0o600)
written=[]
try:
  for p,new in changed.items():
    stat=p.stat()
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(p.parent),prefix='.stage10-product-',delete=False) as fp:
      fp.write(new)
      tmp=fp.name
    os.chown(tmp,stat.st_uid,stat.st_gid)
    os.chmod(tmp,stat.st_mode)
    os.replace(tmp,str(p))
    written.append(p)
  tests=[
    ('/stul-sevyn-bukle-kofeynyy-2-sht/','external'),
    ('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','native'),
    ('/category/kompyuternye-kresla/','other'),
    ('/order/','other')]
  checks=[]
  for path,mode in tests:
    url='https://profikompany.ru'+path+'?stage10_verify='+str(int(time.time()))
    cmd=['curl','-k','-sS','-L','--max-time','35','-w','\n%{http_code}',url]
    p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
    out,err=p.communicate(timeout=43)
    page,status=out.rsplit('\n',1) if '\n' in out else ('','')
    ok=(p.returncode==0 and status=='200')
    details={'path':path,'mode':mode,'http':status}
    if mode=='external':
      one=page.split('class="product-page"')[1].split('</article>')[0] if 'class="product-page"' in page else ''
      has_external='js-extimg-main-img' in one
      details['uses_external_gallery']=has_external
      details['primary_image_schema']='itemprop="image"' in one
      details['sku_schema']='itemprop="sku"' in one
      details['product_url_schema']='itemprop="url"' in one
      if has_external:
        details['external_image_schema']='<img itemprop="image"' in one
        details['lazy_thumbnails']=('loading="lazy" decoding="async"' in one) or 'product-gallery__thumb' not in one
      else:
        details['external_image_schema']='not_applicable'
        details['lazy_thumbnails']=('loading="lazy"' in one) or 'product-photos__list' not in one
      details['main_photo_not_lazy']=not has_external or ('itemprop="image" src=' in one and
          one.index('itemprop="image" src=') < one.find('loading="lazy"') if 'loading="lazy"' in one else 'itemprop="image" src=' in one)
      ok=ok and all(details[k] for k in ['primary_image_schema','sku_schema','product_url_schema','lazy_thumbnails','main_photo_not_lazy'])
      if has_external:ok=ok and details['external_image_schema'] is True
    if mode=='native':
      one=page.split('class="product-page"')[1].split('</article>')[0] if 'class="product-page"' in page else ''
      details['sku_schema']='itemprop="sku"' in one
      details['product_url_schema']='itemprop="url"' in one
      details['gallery_thumbnail_lazy']='loading="lazy"' in one
      details['native_primary_image']='itemprop="image"' in one
      ok=ok and all(details[k] for k in ['sku_schema','product_url_schema','gallery_thumbnail_lazy','native_primary_image'])
    if mode=='other':
      if path=='/order/':
        details['cart_noindex']='name="robots" content="noindex,follow"' in page
        ok=ok and details['cart_noindex']
    details['ok']=ok
    checks.append(details)
  if not all(x['ok'] for x in checks):
    raise RuntimeError('Live product smoke check failed '+json.dumps(checks,ensure_ascii=False))
  print('PRODUCT_STAGE10_APPLY='+json.dumps({'status':'success','backup':str(backupdir),
    'files':[str(p.relative_to(root)) for p in written],'checks':checks},ensure_ascii=False))
except Exception as exc:
  for p in written:shutil.copy2(str(backupdir/p.name),str(p))
  print('PRODUCT_STAGE10_ROLLBACK='+json.dumps({'reason':str(exc),'restored_files':[str(p.relative_to(root)) for p in written]},ensure_ascii=False))
  raise
PY
"""

def run():
 api=os.environ.get("NETANGELS_API_KEY","").strip()
 if not api:raise RuntimeError("NETANGELS_API_KEY missing")
 token=request_json("https://panel.netangels.ru/api/gateway/token/","POST",
    urllib.parse.urlencode({"api_key":api}).encode(),
    {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"storefront-product-stage10/1.0"}).get("token")
 if not token:raise RuntimeError("NetAngels gateway token unavailable")
 h={"Authorization":"Bearer "+token,"Content-Type":"application/json","Accept":"application/json"}
 with tempfile.TemporaryDirectory(prefix="storefront-product-stage10-") as d:
  key=d+"/id_ed25519"
  subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
  pub=Path(key+".pub").read_text().strip()
  data=request_json("https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/".format(VM_ID),"POST",
    json.dumps({"key":pub,"name":"product-stage10-"+os.getenv("GITHUB_RUN_ID","manual")}).encode(),h)
  kid=data.get("id")
  if not kid:raise RuntimeError("Temporary SSH key not added")
  try:
   proc=subprocess.run(["ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
     "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15","root@"+VM_IP,"bash -s"],
     input=REMOTE,text=True,capture_output=True,timeout=175)
   for line in proc.stdout.splitlines():
    if line.startswith("PRODUCT_STAGE10_"):print(line,flush=True)
   if proc.returncode:
    raise RuntimeError("Stage10 product patch failed: "+proc.stdout[-1300:]+proc.stderr[-500:])
  finally:
   try:
    request_json("https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/".format(VM_ID,kid),"DELETE",None,h)
    print("temporary_ssh_key_removed=true",flush=True)
   except Exception:
    print("WARNING: SSH key cleanup needs review")
if __name__=="__main__":
 run()
