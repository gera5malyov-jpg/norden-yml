#!/usr/bin/env python3
import json, re, requests, xml.etree.ElementTree as ET
from collections import Counter
URL='https://s3.q-parser.ru/automata/63971edfdb851/4s-mebel.ru.xml'
OUT='4s-kit/reference_brand_probe.json'
r=requests.get(URL,timeout=180,headers={'User-Agent':'Mozilla/5.0'})
r.raise_for_status()
open('/tmp/ref4s.xml','wb').write(r.content)
vendors=Counter(); params=Counter(); names=Counter(); offers=0
for ev,e in ET.iterparse('/tmp/ref4s.xml',events=('end',)):
    if e.tag.split('}')[-1]=='offer':
        offers+=1
        vendor=''
        for ch in e:
            tag=ch.tag.split('}')[-1]
            if tag=='vendor' and ch.text:
                vendor=ch.text.strip()
            elif tag=='param':
                n=(ch.attrib.get('name') or '').strip()
                v=(ch.text or '').strip()
                if n and v and re.search(r'бренд|производ|марка',n,re.I):
                    params[(n,v)]+=1
        vendors[vendor]+=1
        nm=next(((ch.text or '').strip() for ch in e if ch.tag.split('}')[-1]=='name' and ch.text), '')
        if nm: names[nm]+=1
        e.clear()
data={
 'bytes':len(r.content),
 'offers':offers,
 'vendors':vendors.most_common(100),
 'brand_like_params':[{'name':k[0],'value':k[1],'count':v} for k,v in params.most_common(200)],
}
import os; os.makedirs('4s-kit',exist_ok=True)
open(OUT,'w',encoding='utf-8').write(json.dumps(data,ensure_ascii=False,indent=2))
print(json.dumps(data,ensure_ascii=False,indent=2))
