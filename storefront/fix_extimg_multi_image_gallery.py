#!/usr/bin/env python3
"""Safely show every [extimg] photo in Shop-Script external image galleries.

The supplier summary stays intact for downstream integrations. No media files
are uploaded to Webasyst or KIT. Changes only the active storefront theme,
with a private backup and automatic rollback on smoke-test failure.
"""
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from stage52_disable_callback import execute


START = "{* === extimg: parse external image links from product.summary === *}"
END = "{* === /extimg === *}"
NEW_PARSER = r'''{* === extimg: parse ALL [extimg] blocks, one URL per block or legacy multiple lines === *}
{assign var="ext_images" value=[]}
{assign var="__extimg_source" value=$product.summary|default:''}
{assign var="__has_extimg" value=false}

{if $__extimg_source}
    {if preg_match_all('/\[extimg\](.*?)\[\/extimg\]/is', $__extimg_source, $__extimg_matches)}
        {assign var="__has_extimg" value=true}
        {foreach $__extimg_matches.1 as $__extimg_block}
            {assign var="__extimg_block" value=str_replace("\r\n", "\n", $__extimg_block)}
            {assign var="__extimg_block" value=str_replace("\r", "\n", $__extimg_block)}
            {assign var="__lines" value=explode("\n", $__extimg_block)}
            {foreach $__lines as $__line}
                {assign var="__url" value=trim($__line)}
                {if $__url && preg_match('~^https?://~i', $__url) && !in_array($__url, $ext_images)}
                    {append var="ext_images" value=$__url}
                {/if}
            {/foreach}
        {/foreach}
    {/if}
{/if}
{* === /extimg === *}'''


def patch_template(text):
    if text.count(START) != 1 or text.count(END) != 1:
        raise ValueError("Expected external-image parser markers not unique")
    begin = text.index(START)
    end = text.index(END) + len(END)
    previous = text[begin:end]
    if "preg_match_all" in previous:
        raise ValueError("Gallery parser already updated; refusing repeat write")
    if "preg_match('/\\[extimg\\](.*?)\\[\\/extimg\\]/is'" not in previous:
        raise ValueError("Unexpected external-image parser, abort patch")
    updated = text[:begin] + NEW_PARSER + text[end:]
    # Thumbnail URLs are external. Defer loading until thumbnails approach the viewport.
    fragment = '<img src="{$img_url|escape}"'
    if updated.count(fragment) == 1:
        updated = updated.replace(fragment, '<img loading="lazy" decoding="async" src="{$img_url|escape}"', 1)
    if not ("product-gallery--extimg" in updated and "js-extimg-thumb" in updated):
        raise ValueError("The existing image gallery is missing")
    return updated


REMOTE = r'''set -eu
python3 - <<'PY'
import hashlib,json,os,pathlib,shutil,subprocess,tempfile,time,urllib.parse
from datetime import datetime, timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
rel='wa-data/public/shop/themes/pureMegapolis42/product.html'
path=root/rel
source=path.read_text(encoding='utf-8')
START_REPR
END_REPR
PARSER_REPR
def patch_template(text):
    if text.count(START) != 1 or text.count(END) != 1:
        raise RuntimeError('External-image parser markers changed')
    first=text.index(START); last=text.index(END)+len(END)
    old=text[first:last]
    if 'preg_match_all' in old:
        raise RuntimeError('External image parser already patched')
    if "preg_match('/\\[extimg\\](.*?)\\[\\/extimg\\]/is'" not in old:
        raise RuntimeError('Unexpected extimg parser')
    result=text[:first]+NEW_PARSER+text[last:]
    tag='<img src="{$img_url|escape}"'
    if result.count(tag)==1:
        result=result.replace(tag,'<img loading="lazy" decoding="async" src="{$img_url|escape}"',1)
    if 'product-gallery--extimg' not in result or 'js-extimg-thumb' not in result:
        raise RuntimeError('External gallery rendering missing')
    return result

new=patch_template(source)
old_sha=hashlib.sha256(source.encode()).hexdigest()
new_sha=hashlib.sha256(new.encode()).hexdigest()
if MODE_REPR=='audit':
    print('RIVA_GALLERY_AUDIT='+json.dumps({'source_sha':old_sha,'expected_photo_blocks':11,'old_uses_single_match':True,'new_uses_all_matches':True,'changes_only_template':rel},ensure_ascii=False),flush=True)
else:
    folder=root.parent/'storefront-backups'/('riva-extimg-gallery-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
    folder.mkdir(parents=True,mode=0o700)
    backup=folder/'product.html.bak'
    shutil.copy2(str(path),str(backup));os.chmod(str(backup),0o600)
    stat=path.stat()
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(path.parent),prefix='.riva-extimg-',delete=False) as f:
        f.write(new);tmp=f.name
    os.chmod(tmp,stat.st_mode&0o777);os.chown(tmp,stat.st_uid,stat.st_gid)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=old_sha:
        os.unlink(tmp);raise RuntimeError('Concurrent template modification')
    os.replace(tmp,str(path))
    try:
        route='/'+urllib.parse.quote('Кресло-Икспресс-CX1361М-Черный-Пластик-Черный')+'/'
        tests=[]
        # Template caches can take a moment to observe an updated Smarty template.
        for attempt in range(5):
            results=[]
            for target in (route,'/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/'):
                u='https://profikompany.ru'+target+'?extimg_test='+str(os.getpid())+'_'+str(attempt)
                p=subprocess.run(['curl','-sSL','--max-time','40','-w','\n%{http_code}',u],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=47)
                html,status=p.stdout.rsplit('\n',1) if '\n' in p.stdout else ('','')
                count=html.count('data-extimg="')
                ok=p.returncode==0 and status=='200'
                if target==route:
                    ok=ok and count==11 and html.count('js-extimg-main-img')>=1 and '[extimg]' not in html
                results.append({'product':target==route,'http_status':status,'thumbnail_count':count,'ok':ok})
            tests=results
            if all(x['ok'] for x in results):break
            time.sleep(2)
        if not all(x['ok'] for x in tests):
            raise RuntimeError('Storefront smoke failed: '+json.dumps(tests,ensure_ascii=False))
        print('RIVA_GALLERY_DEPLOY='+json.dumps({'status':'success','backup':str(backup),'old_sha':old_sha,'new_sha':new_sha,'tests':tests},ensure_ascii=False),flush=True)
    except Exception as exc:
        if hashlib.sha256(path.read_bytes()).hexdigest()!=new_sha:
            raise RuntimeError('Live template changed; refusing unsafe rollback') from exc
        shutil.copy2(str(backup),str(path));os.utime(str(path),None)
        print('RIVA_GALLERY_ROLLBACK='+json.dumps({'status':'restored','reason':str(exc)},ensure_ascii=False),flush=True)
        raise
PY
'''


def main():
    assert len(sys.argv) == 2 and sys.argv[1] in ("audit", "apply")
    remote = REMOTE.replace("START_REPR", "START=" + repr(START))
    remote = remote.replace("END_REPR", "END=" + repr(END))
    remote = remote.replace("PARSER_REPR", "NEW_PARSER=" + repr(NEW_PARSER))
    remote = remote.replace("MODE_REPR", repr(sys.argv[1]))
    result = execute(remote)
    indicator = "RIVA_GALLERY_AUDIT=" if sys.argv[1] == "audit" else "RIVA_GALLERY_DEPLOY="
    if indicator not in result:
        raise RuntimeError("Expected guarded deployment report not found")


if __name__ == "__main__":
    main()
