#!/usr/bin/env python3
"""Guarded WOFF-to-WOFF2 optimization of existing Montserrat faces.

Converts existing site-owned web-font assets byte-for-byte in semantics while
preserving existing WOFF fallback; deploys only new files and a scoped
stylesheet reference after 3 preflights and full backup.
"""
import base64,concurrent.futures,hashlib,io,json,re,time,urllib.request,zipfile
from fontTools.ttLib import TTFont
from stage52_disable_callback import execute

SITE="https://profikompany.ru"
PUB="/wa-data/public/site/themes/pureMegapolis42/css/fonts"
OLD_CSS="montserrat.megapolis75.css"
NEW_CSS="montserrat.megapolis76.css"
REF_OLD="montserrat.megapolis75.css?v=20261019"
REF_NEW="montserrat.megapolis76.css?v=20261019b"

def fetch(path):
    url=SITE+PUB+"/"+path
    for step in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 (site performance audit)"}),timeout=35) as r:
                data=r.read()
                if r.status!=200 or not data:
                    raise RuntimeError("HTTP status or empty font")
                return data
        except Exception:
            if step==2:
                raise RuntimeError("Failed to read existing site font: "+path)
            time.sleep(2*(step+1))

def convert(entry):
    name=entry
    data=fetch(name+".woff")
    source=TTFont(io.BytesIO(data),lazy=False)
    glyphs=len(source.getBestCmap() or {})
    if glyphs<50:
        raise RuntimeError("Unexpected glyph count in "+name)
    source.flavor="woff2"
    out=io.BytesIO()
    source.save(out)
    dst=out.getvalue()
    verify=TTFont(io.BytesIO(dst),lazy=False)
    if len(verify.getBestCmap() or {})!=glyphs or dst[:4]!=b"wOF2":
        raise RuntimeError("WOFF2 validation failed: "+name)
    return name,dst,len(data),glyphs

def main():
    css=fetch(OLD_CSS).decode("utf8")
    pat=r"url\('(?P<name>montserrat-megapolis53-\d{2})\.woff'\) format\(\"woff\"\)"
    face_names=re.findall(pat,css)
    if len(face_names)!=18 or len(set(face_names))!=18:
        raise RuntimeError("Font definitions changed or incomplete")
    print("FONT76_PREFLIGHT="+json.dumps({"check":1,"ok":True,"faces":len(face_names)}),flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        results=list(ex.map(convert,face_names))
    total_before=sum(row[2] for row in results)
    total_after=sum(len(row[1]) for row in results)
    if total_after>=total_before*.88:
        raise RuntimeError("WOFF2 conversion savings below threshold")
    print("FONT76_PREFLIGHT="+json.dumps({"check":2,"ok":True,"total_woff_kb":round(total_before/1024,1),
      "total_woff2_kb":round(total_after/1024,1),"saving_percent":round((1-total_after/total_before)*100,1)}),flush=True)
    new_css,count=re.subn(pat,lambda match:
      "url('"+match.group('name')+".woff2') format(\"woff2\"),url('"+match.group('name')+".woff') format(\"woff\")",css)
    if count!=18 or new_css.count("font-display:optional")!=18:
        raise RuntimeError("Font CSS semantics changed unexpectedly")
    print("FONT76_PREFLIGHT="+json.dumps({"check":3,"ok":True,"woff_fallback_preserved":True}),flush=True)
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_STORED) as zipf:
        zipf.writestr(NEW_CSS,new_css.encode("utf8"))
        for name,content,old_len,glyphs in results:
            zipf.writestr(name+".woff2",content)
    encoded=base64.b64encode(archive.getvalue()).decode("ascii")
    remote=StringRemoteTemplate(encoded)
    result=execute(remote)
    for line in result.splitlines():
        if line.startswith(("FONT76_BACKUP=","FONT76_TEST=","FONT76_DEPLOY=","FONT76_ROLLBACK=","FONT76_SERVER_PREFLIGHT=")):
            print(line,flush=True)
    if "FONT76_DEPLOY=" not in result:
        raise RuntimeError("WOFF2 deployment did not verify")
    print("FONT76_BUILD="+json.dumps({"converted":18,"source_kb":round(total_before/1024,1),
      "woff2_kb":round(total_after/1024,1),"saving_percent":round((1-total_after/total_before)*100,1)}),flush=True)

def StringRemoteTemplate(encoded):
    return r'''set -eu
python3 - <<'PY'
import base64,datetime,hashlib,io,json,os,pathlib,re,shutil,subprocess,tempfile,zipfile
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
theme=root/'wa-data/public/site/themes/pureMegapolis42'
fonts=theme/'css/fonts'
template=None
old_name="montserrat.megapolis75.css?v=20261019"
new_name="montserrat.megapolis76.css?v=20261019b"
payload=base64.b64decode("'''+encoded+r'''")
candidates=[]
for folder in (theme,root/'wa-data/public/shop/themes/pureMegapolis42'):
 for candidate in folder.rglob('*.html'):
  if old_name in candidate.read_text('utf8',errors='replace'):
   candidates.append(candidate)
if len(candidates)!=1:raise RuntimeError('Expected exactly one active theme font reference; found '+str(len(candidates)))
template=candidates[0]
source=template.read_bytes()
text=source.decode("utf8")
zipfile_data=zipfile.ZipFile(io.BytesIO(payload))
entries=zipfile_data.namelist()
woff2=[name for name in entries if re.match(r'^montserrat-megapolis53-\d{2}\.woff2$',name)]
new_assets=[fonts/name for name in entries]
if text.count(old_name)!=1 or new_name in text:
 raise RuntimeError('Theme font reference guard: original_count='+str(text.count(old_name))+' current_new='+str(text.count(new_name))+' template='+str(template.relative_to(root)))
if len(entries)!=19 or len(woff2)!=18 or len(set(entries))!=19 or any(p.exists() for p in new_assets):
 raise RuntimeError('Unexpected bundle or asset collision; do not edit')
old_css=fonts/'montserrat.megapolis75.css'
if not old_css.is_file() or old_css.read_text('utf8').count('font-display:optional')!=18:
 raise RuntimeError('Old fonts stylesheet changed; do not edit')
new_style=zipfile_data.read('montserrat.megapolis76.css')
if new_style.count(b'.woff2')!=18 or new_style.count(b'.woff')!=36:
 raise RuntimeError('New fonts stylesheet did not preserve fallback')
print('FONT76_SERVER_PREFLIGHT='+json.dumps({'assets_to_create':len(entries),'one_theme_file':True}),flush=True)
backup_folder=root.parent/'storefront-backups'/('font76-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
backup_folder.mkdir(parents=True,mode=0o700)
backup=backup_folder/'index.html'
shutil.copy2(str(template),str(backup));os.chmod(str(backup),0o600)
if backup.read_bytes()!=source:raise RuntimeError('Font backup mismatch')
print('FONT76_BACKUP='+json.dumps({'file':str(backup),'source_sha':hashlib.sha256(source).hexdigest()}),flush=True)
written=[]
theme_written=False
updated=text.replace(old_name,new_name,1).encode('utf8')
try:
 for name in entries:
  if '/' in name or '\\' in name:raise RuntimeError('Path traversal')
  content=zipfile_data.read(name)
  if not content:raise RuntimeError('Empty font asset')
  output=fonts/name
  with tempfile.NamedTemporaryFile('wb',dir=str(fonts),prefix='.font76-',delete=False) as tmp:
   tmp.write(content);tmp_name=tmp.name
  os.chmod(tmp_name,0o644)
  if output.exists():raise RuntimeError('Asset conflict')
  os.rename(tmp_name,str(output))
  written.append(output)
  if output.read_bytes()!=content:raise RuntimeError('Asset write validation failed')
 with tempfile.NamedTemporaryFile('wb',dir=str(template.parent),prefix='.font76-theme-',delete=False) as tmp:
  tmp.write(updated); tmp_name=tmp.name
 st=template.stat()
 os.chmod(tmp_name,st.st_mode&0o777);os.chown(tmp_name,st.st_uid,st.st_gid)
 if template.read_bytes()!=source:raise RuntimeError('Concurrent theme edit')
 os.replace(tmp_name,str(template));theme_written=True
 paths=[
 ('/','site'),('/category/kompyuternye-kresla/','site'),
 ('/category/kompyuternye-kresla/?page=2','site'),
 ('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','site'),
 ('/stul-prozrachnyy-pedrali-queen/','site'),
 ('/oplata/','site'),('/dostavka/','site'),('/o-kompanii/','site'),
 ('/order/','site'),('/garantiya/','site')]
 for i,(path,kind) in enumerate(paths):
  p=subprocess.Popen(['curl','-ksSL','--compressed','--max-time','28','-w','\n%{http_code}',
   'https://profikompany.ru'+path],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  result,error=p.communicate(timeout=33)
  html,status=result.rsplit('\n',1) if '\n' in result else ('','')
  ok=(p.returncode==0 and status=='200' and new_name in html and old_name not in html and '<title' in html and '+7 (812)' in html)
  print('FONT76_TEST='+json.dumps({'i':i+1,'url':path,'ok':bool(ok),'status':status}),flush=True)
  if not ok:raise RuntimeError('Regression check '+str(i+1)+' failed')
 if template.read_bytes()!=updated:raise RuntimeError('Unexpected theme mutation')
 print('FONT76_DEPLOY='+json.dumps({'ok':True,'tests':10,'files':len(written)+1,'backup':str(backup),
  'old_woff_retained':True,'prices_stocks_yml_untouched':True},ensure_ascii=False),flush=True)
except Exception as err:
 if theme_written and template.read_bytes()==updated:
  shutil.copy2(str(backup),str(template))
 for file in written:
  if file.exists():file.unlink()
 print('FONT76_ROLLBACK='+json.dumps({'ok':True,'reason':str(err)[:350]}),flush=True)
 raise
PY
'''

if __name__=="__main__":main()
