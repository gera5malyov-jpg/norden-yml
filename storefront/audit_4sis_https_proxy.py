#!/usr/bin/env python3
"""Read-only server audit for 4sis legacy HTTP image links and active nginx/theme."""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from stage52_disable_callback import execute
REMOTE=r"""set -eu
python3 - <<'PY'
import json,pathlib,re,subprocess
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
template=root/'wa-data/public/shop/themes/pureMegapolis42/product.html'
out={'template_exists':template.exists()}
if template.exists():
 s=template.read_text(errors='replace')
 lines=s.splitlines()
 matches=[i for i,l in enumerate(lines) if 'extimg' in l.lower() or 'img_url' in l or 'js-extimg-main' in l]
 out['gallery_lines']=[{'n':i+1,'line':lines[i][:310]} for i in matches[:85]]
 out['external_img_block_count']=s.count('extimg: parse ALL')
out['nginx_present']=bool(subprocess.run(['bash','-lc','command -v nginx'],stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout)
candidates=[]
for dirname in ['/etc/nginx/sites-enabled','/etc/nginx/conf.d','/etc/nginx/vhosts','/etc/nginx/sites-available']:
 p=pathlib.Path(dirname)
 if not p.is_dir():continue
 for path in p.rglob('*'):
  if not path.is_file() or len(candidates)>70:continue
  try:s=path.read_text(errors='replace')
  except Exception:continue
  if ('profikompany.ru' in s or '/home/web/vm-23f9aff9' in s) and len(s)<100000:
   candidates.append({'path':str(path),'server_name_lines':[l.strip()[:210] for l in s.splitlines() if ('server_name' in l or 'root ' in l or 'include ' in l or 'listen ' in l or 'server {' in l or 'proxy_pass' in l)][:42],'has_proxy4sis':'4sis-images' in s})
out['server_configs']=candidates
p=subprocess.run(['nginx','-t'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
out['nginx_test']={'returncode':p.returncode,'stderr':p.stderr[-480:]}
print('EXTIMG_AUDIT='+json.dumps(out,ensure_ascii=False),flush=True)
PY
"""
def main():
 result=execute(REMOTE)
 if 'EXTIMG_AUDIT=' not in result:raise RuntimeError('Missing server audit result')
if __name__=='__main__':main()
