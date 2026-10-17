#!/usr/bin/env python3
"""Fix mixed-content 4sis photos without uploading media or rewriting product DB.

Guarded nginx HTTPS reverse proxy for a *fixed* legacy supplier HTTP image IP,
and one minimal product theme URL rewrite for [extimg] blocks.
Backs up both files on server; auto rolls back and reloads if smoke fails.
"""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from stage52_disable_callback import execute

REMOTE=r"""set -eu
python3 - <<'PY'
import hashlib,json,os,pathlib,re,shutil,subprocess,tempfile,time
from datetime import datetime
ROOT=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
THEME=ROOT/'wa-data/public/shop/themes/pureMegapolis42/product.html'
CUSTOM=pathlib.Path('/etc/nginx/custom-settings.d/profikompany.ru.conf')
PREFIX='http://5.35.127.160/images/'
PATH_PREFIX='/4sis-images/'
BEGIN='# BEGIN 4sis HTTPS legacy image proxy (ChatGPT-managed)'
END='# END 4sis HTTPS legacy image proxy (ChatGPT-managed)'
SAMPLE='1c/38342043352038452045372033452033-001.jpg'
PUBLIC_PAGE='https://profikompany.ru/barselona-kofeynyy-stolik-iz-iskusstvennogo-rotanga-tsvet-korichnevyy/'
nginx_block=r'''
# BEGIN 4sis HTTPS legacy image proxy (ChatGPT-managed)
# Fixed supplier image origin; no arbitrary URL / SSRF proxying.
location ^~ /4sis-images/ {
    if ($uri !~* "^/4sis-images/[a-z0-9_/-]+\.(jpe?g|png|webp)$") {
        return 404;
    }
    proxy_pass http://5.35.127.160/images/;
    proxy_set_header Host 5.35.127.160;
    proxy_set_header Connection "";
    proxy_connect_timeout 8s;
    proxy_read_timeout 30s;
    proxy_send_timeout 20s;
    proxy_buffering on;
    proxy_hide_header Set-Cookie;
    add_header Cache-Control "public, max-age=86400" always;
    add_header X-Content-Type-Options "nosniff" always;
}
# END 4sis HTTPS legacy image proxy (ChatGPT-managed)
'''
old_fragment='''                {assign var="__url" value=trim($__line)}
                {if $__url && preg_match('~^https?://~i', $__url) && !in_array($__url, $ext_images)}
                    {append var="ext_images" value=$__url}
                {/if}'''
new_fragment='''                {assign var="__url" value=str_replace('http://5.35.127.160/images/', 'https://profikompany.ru/4sis-images/', trim($__line))}
                {if $__url && preg_match('~^https?://~i', $__url) && !in_array($__url, $ext_images)}
                    {append var="ext_images" value=$__url}
                {/if}'''
if not THEME.is_file() or not CUSTOM.is_file():
    raise RuntimeError('Missing expected theme or custom nginx include: '+str(THEME)+' '+str(CUSTOM))
old_theme=THEME.read_text(encoding='utf-8')
old_custom=CUSTOM.read_text(encoding='utf-8')
if old_theme.count(old_fragment)!=1:
    raise RuntimeError('Unexpected active extimg parser. Aborting before write. count='+str(old_theme.count(old_fragment)))
if BEGIN in old_custom or '/4sis-images/' in old_custom:
    raise RuntimeError('4sis proxy already configured; refusing duplicate insertion')
if old_custom and not old_custom.endswith('\n'):
    new_custom=old_custom+'\n'+nginx_block
else:
    new_custom=old_custom+nginx_block
new_theme=old_theme.replace(old_fragment,new_fragment,1)
# Fix pre-existing doubled lazy-load attributes, no functional change.
new_theme=new_theme.replace('loading="lazy" decoding="async" src="{$img_url|escape}" loading="lazy" decoding="async"',
                              'loading="lazy" decoding="async" src="{$img_url|escape}"')
if new_theme==old_theme:raise RuntimeError('Theme patch unexpectedly empty')
archive=ROOT.parent/'storefront-backups'/('4sis-https-image-proxy-'+datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
archive.mkdir(parents=True,mode=0o700)
theme_bak=archive/'product.html.bak'
nginx_bak=archive/'profikompany-custom-nginx.conf.bak'
shutil.copy2(str(THEME),str(theme_bak))
shutil.copy2(str(CUSTOM),str(nginx_bak))
os.chmod(str(theme_bak),0o600)
os.chmod(str(nginx_bak),0o600)
def atomic(p,content):
    stat=p.stat()
    fd,path=tempfile.mkstemp(prefix='.4sis-image-',dir=str(p.parent))
    try:
        with os.fdopen(fd,'w') as handle:handle.write(content)
        os.chmod(path,stat.st_mode&0o777)
        os.chown(path,stat.st_uid,stat.st_gid)
        os.rename(path,str(p))
    finally:
        if os.path.exists(path):os.unlink(path)
def cmd(args,timeout=40):
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=timeout)
    return p
def test_mime():
    p=cmd(['curl','-k','-sS','--resolve','profikompany.ru:443:127.0.0.1',
         '-o','/dev/null','--max-time','22','-w','%{http_code}|%{content_type}|%{size_download}',
         'https://profikompany.ru/4sis-images/'+SAMPLE],timeout=30)
    return {'code':p.returncode,'http':p.stdout,'err':p.stderr[-160:]}
try:
    if hashlib.sha256(THEME.read_bytes()).digest()!=hashlib.sha256(old_theme.encode('utf-8')).digest():
        raise RuntimeError('Concurrent theme modifications')
    atomic(CUSTOM,new_custom)
    p=cmd(['nginx','-t'])
    if p.returncode!=0:
        raise RuntimeError('NGINX_TEST_FAILED '+p.stderr[-900:])
    atomic(THEME,new_theme)
    p=cmd(['nginx','-s','reload'])
    if p.returncode!=0:
        raise RuntimeError('NGINX_RELOAD_FAILED '+p.stderr[-500:])
    results=[]
    for attempt in range(8):
        image=test_mime()
        html=cmd(['curl','-k','-LsS','--max-time','25','-w','\\n%{http_code}',
             PUBLIC_PAGE+'?extimg_proxy_fix='+str(os.getpid())+'_'+str(attempt)],timeout=35)
        raw=html.stdout
        body,http_status=raw.rsplit('\\n',1) if '\\n' in raw else (raw,'unknown')
        expected='src="https://profikompany.ru/4sis-images/'+SAMPLE+'"'
        has_img=expected in body
        old_img='src="'+PREFIX+SAMPLE+'"'
        old_found=old_img in body
        result={'try':attempt,'proxy':image,'html_http_ok':html.returncode==0 and http_status=='200',
                'html_status':http_status,'html_bytes':len(body),'html_start':body[:140],
                'patched_main':has_img,'old_http_main':old_found,
                'rewrite_url_instances':body.count('/4sis-images/'),
                'thumb_count':body.count('data-extimg="https://profikompany.ru/4sis-images/')}
        results.append(result)
        if image['code']==0 and image['http'].startswith('200|image/jpeg|') and has_img and not old_found:break
        time.sleep(2)
    last=results[-1]
    if not (last['proxy']['code']==0 and last['proxy']['http'].startswith('200|image/jpeg|')
            and last['patched_main'] and not last['old_http_main']):
        raise RuntimeError('Smoke failed '+json.dumps(last))
    print('STAGE52_EXTIMG_PROXY='+json.dumps({
        'result':'success','backup_dir':str(archive),
        'path':'/4sis-images/','supplier_origin':'http://5.35.127.160/images/',
        'smoke':last,'checks':len(results)
    }),flush=True)
except Exception as exc:
    shutil.copy2(str(theme_bak),str(THEME))
    shutil.copy2(str(nginx_bak),str(CUSTOM))
    cmd(['nginx','-t'])
    cmd(['nginx','-s','reload'])
    print('STAGE52_EXTIMG_PROXY='+json.dumps({
        'result':'rolled_back','backup_dir':str(archive),'error':str(exc)[:1800]
    }),flush=True)
    raise
PY
"""
def main():
    output=execute(REMOTE)
    if '"result": "success"' not in output and '"result":"success"' not in output:
        raise RuntimeError('No successful guarded HTTPS image proxy validation report')
if __name__=='__main__':main()
