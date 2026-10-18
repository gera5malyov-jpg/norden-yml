#!/usr/bin/env python3
import json
from stage52_disable_callback import execute
REMOTE = r'''set -eu
python3 - <<'PY'
import hashlib,pathlib,json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
for relative in ['wa-data/public/site/themes/pureMegapolis42/index.html','wa-data/public/shop/themes/pureMegapolis42/category.html']:
    path=root/relative
    if not path.is_file():
        print('META_FILE='+json.dumps({'file':relative,'exists':False}),flush=True)
        continue
    data=path.read_bytes()
    lines=data.decode('utf-8','replace').splitlines()
    marks=['<title>', '_info_title', '_info_description','meta name="description"','meta(\'description\')','Scoped SEO improvements','megapolis-native-category-meta-stage55']
    found=set()
    for i,line in enumerate(lines):
        if any(mark in line for mark in marks):
            for j in range(max(0,i-4),min(len(lines),i+6)):
                found.add(j)
    sections=[{'line':j+1,'text':lines[j][:450]} for j in sorted(found)][:200]
    print('META_FILE='+json.dumps({'file':relative,'exists':True,'sha256':hashlib.sha256(data).hexdigest(),'lines':len(lines),'sections':sections},ensure_ascii=False),flush=True)
PY
'''
res=execute(REMOTE)
for line in res.splitlines():
    if line.startswith('META_FILE='):
        print(line,flush=True)
