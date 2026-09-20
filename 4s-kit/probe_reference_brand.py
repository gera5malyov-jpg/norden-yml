#!/usr/bin/env python3
import json, re, requests, xml.etree.ElementTree as ET
from collections import Counter
URL='https://s3.q-parser.ru/automata/63971edfdb851/4s-mebel.ru.xml'
OUT='4s-kit/reference_brand_probe.json'
r=requests.get(URL,timeout=180,headers={'User-Agent':'Mozilla/5.0'})
r.raise_for_status()
open('/tmp/ref4s.xml','wb').write(r.content)
vendors=Counter(); params=Counter(); names=Counter(); codes=Counter(); code_prefixes=Counter(); examples=[]; offers=0
for ev,e in ET.iterparse('/tmp/ref4s.xml',events=('end',)):
    if e.tag.split('}')[-1]=='offer':
        offers+=1
        vendor=''; code=''
        for ch in e:
            tag=ch.tag.split('}')[-1]
            if tag=='vendor' and ch.text:
                vendor=ch.text.strip()
            elif tag in ('vendorCode','model','sku') and ch.text and not code:
                code=ch.text.strip()
            elif tag=='param':
                n=(ch.attrib.get('name') or '').strip()
                v=(ch.text or '').strip()
                if n and v and re.search(r'бренд|производ|марка',n,re.I):
                    params[(n,v)]+=1
        vendors[vendor]+=1
        if code:
            codes[code]+=1
            pref=(code.split('-',1)[0]+'-') if '-' in code else code[:8]
            code_prefixes[pref]+=1
            if code.lower().startswith('4s-') and len(examples)<30:
                examples.append(code)
        nm=next(((ch.text or '').strip() for ch in e if ch.tag.split('}')[-1]=='name' and ch.text), '')
        if nm: names[nm]+=1
        e.clear()
data={
 'bytes':len(r.content),
 'offers':offers,
 'vendors':vendors.most_common(100),
 'codes_total':sum(codes.values()),
 'codes_4s_prefix':sum(v for k,v in codes.items() if k.lower().startswith('4s-')),
 'code_prefixes':code_prefixes.most_common(100),
 'examples_4s':examples,
 'brand_like_params':[{'name':k[0],'value':k[1],'count':v} for k,v in params.most_common(200)],
}
import os; os.makedirs('4s-kit',exist_ok=True)
open(OUT,'w',encoding='utf-8').write(json.dumps(data,ensure_ascii=False,indent=2))
print(json.dumps(data,ensure_ascii=False,indent=2))
