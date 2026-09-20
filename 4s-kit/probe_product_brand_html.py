#!/usr/bin/env python3
import json,re,requests
from bs4 import BeautifulSoup
URL='https://4s-mebel.ru/product/stul_aysberg_loft_01_chernyy_chernyy/'
html=requests.get(URL,timeout=90,headers={'User-Agent':'Mozilla/5.0'}).text
soup=BeautifulSoup(html,'lxml')
scripts=[]
for sc in soup.find_all('script'):
    txt=sc.string or sc.get_text(' ',strip=True)
    if re.search(r'brand|vendor|manufacturer|бренд|производител|4 ?сезон',txt,re.I):
        scripts.append(txt[:5000])
snips=[]
for m in re.finditer(r'brand|vendor|manufacturer|бренд|производител|4 ?сезон',html,re.I):
    snips.append(html[max(0,m.start()-250):m.start()+700])
    if len(snips)>=50: break
attrs=[]
for tag in soup.find_all(True):
    for k,v in tag.attrs.items():
        vv=' '.join(v) if isinstance(v,list) else str(v)
        if re.search(r'brand|vendor|manufacturer|бренд|производител|4 ?сезон',k+' '+vv,re.I):
            attrs.append({'tag':tag.name,'attr':k,'value':vv[:1000]})
            if len(attrs)>=100: break
    if len(attrs)>=100: break
data={'url':URL,'len':len(html),'snippets':snips,'attrs':attrs,'scripts':scripts[:20]}
import os; os.makedirs('4s-kit',exist_ok=True)
open('4s-kit/product_brand_html_probe.json','w',encoding='utf-8').write(json.dumps(data,ensure_ascii=False,indent=2))
print(json.dumps(data,ensure_ascii=False,indent=2))
