from __future__ import annotations
import gzip, hashlib, json, os, re, time
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE='https://4s-mebel.ru'
OUT=Path('4s-mebel.yml')
MIN=int(os.getenv('FOURS_MIN_OFFERS','150'))
DELAY=float(os.getenv('FOURS_REQUEST_DELAY','0.12'))
TIMEOUT=45

s=requests.Session()
s.headers.update({'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36 4s-yml/1.0','Accept-Language':'ru-RU,ru;q=0.9'})
s.mount('https://',HTTPAdapter(max_retries=Retry(total=4,backoff_factor=1,status_forcelist=[429,500,502,503,504],allowed_methods=['GET'],respect_retry_after_header=True)))

def txt(v): return re.sub(r'\s+',' ',str(v or '')).strip()
def norm(u):
    p=urlparse(urljoin(BASE,u)); path=re.sub(r'/{2,}','/',p.path)
    if path and not path.endswith('/') and not Path(path).suffix: path+='/'
    return urlunparse((p.scheme or 'https',p.netloc.lower(),path,'',p.query,''))
def get(u,allow404=False):
    r=s.get(u,timeout=TIMEOUT)
    if not(allow404 and r.status_code==404): r.raise_for_status()
    if DELAY: time.sleep(DELAY)
    return r

def sitemap_products():
    q=deque([BASE+'/sitemap.xml',BASE+'/sitemap_index.xml',BASE+'/sitemap_index.xml.gz']); seen=set(); products=set()
    while q and len(seen)<100:
        u=q.popleft()
        if u in seen: continue
        seen.add(u)
        try:
            r=get(u,True)
            if r.status_code==404: continue
            data=r.content
            if u.endswith('.gz') or data[:2]==b'\x1f\x8b': data=gzip.decompress(data)
            root=ET.fromstring(data)
            locs=[txt(e.text) for e in root.iter() if e.tag.split('}')[-1]=='loc' and e.text]
        except Exception as e:
            print('sitemap skip',u,e); continue
        for loc in locs:
            v=norm(loc); p=urlparse(v).path.lower()
            if '/product/' in p: products.add(v.split('?',1)[0])
            elif 'sitemap' in p and v not in seen: q.append(v)
    print('sitemap products',len(products)); return products

def catalog_products():
    q=deque([BASE+'/catalog/']); seen=set(); products=set()
    while q and len(seen)<600:
        u=q.popleft()
        if u in seen: continue
        seen.add(u)
        try: soup=BeautifulSoup(get(u).text,'lxml')
        except Exception as e: print('catalog skip',u,e); continue
        for a in soup.find_all('a',href=True):
            v=norm(urljoin(u,a['href']))
            if urlparse(v).netloc not in ('4s-mebel.ru','www.4s-mebel.ru'): continue
            p=urlparse(v).path.lower()
            if '/product/' in p: products.add(v.split('?',1)[0])
            elif p.startswith('/catalog/') and v not in seen: q.append(v)
    print('catalog products',len(products)); return products

def discover():
    urls=sitemap_products()
    print('catalog fallback disabled for normal runs')
    return sorted(urls)

def walk(v):
    if isinstance(v,dict):
        yield v
        for x in v.values(): yield from walk(x)
    elif isinstance(v,list):
        for x in v: yield from walk(x)

def product_ld(soup):
    for sc in soup.find_all('script',type=re.compile('ld\\+json',re.I)):
        try: data=json.loads(sc.string or sc.get_text(' ',strip=True))
        except Exception: continue
        for o in walk(data):
            t=o.get('@type'); ts=t if isinstance(t,list) else [t]
            if any(str(x).lower()=='product' for x in ts if x): return o
    return {}
def offer_ld(p):
    o=p.get('offers') or {}; return next((x for x in o if isinstance(x,dict)),{}) if isinstance(o,list) else (o if isinstance(o,dict) else {})
def meta(soup,**kw):
    t=soup.find('meta',attrs=kw); return txt(t.get('content')) if t else ''
def price(v):
    m=re.search(r'\d+(?:[.,]\d+)?',txt(v).replace(' ','')); return m.group(0).replace(',','.') if m else ''
def first(*a):
    for x in a:
        if txt(x): return txt(x)
    return ''

def params(soup):
    d={}
    for tr in soup.find_all('tr'):
        c=tr.find_all(['th','td'])
        if len(c)==2:
            k=txt(c[0].get_text(' ',strip=True)).strip(' :'); v=txt(c[1].get_text(' ',strip=True))
            if k and v and len(k)<=120 and len(v)<=1000: d.setdefault(k,v)
    for dl in soup.find_all('dl'):
        for dt,dd in zip(dl.find_all('dt'),dl.find_all('dd')):
            k=txt(dt.get_text(' ',strip=True)).strip(' :'); v=txt(dd.get_text(' ',strip=True))
            if k and v: d.setdefault(k,v)
    bad={'цена','стоимость','количество','итого','название товара','ваше имя','телефон','электронная почта'}
    return {k:v for k,v in d.items() if k.lower() not in bad}

def images(soup,p,url):
    a=[]; raw=p.get('image')
    if isinstance(raw,str): a.append(raw)
    elif isinstance(raw,list):
        for x in raw: a.append(x if isinstance(x,str) else first(x.get('url'),x.get('contentUrl')) if isinstance(x,dict) else '')
    elif isinstance(raw,dict): a.append(first(raw.get('url'),raw.get('contentUrl')))
    a.append(meta(soup,property='og:image'))
    for sel in ["[class*='product'] img","[class*='gallery'] img","[class*='detail'] img"]:
        for im in soup.select(sel): a.append(first(im.get('data-src'),im.get('data-lazy'),im.get('data-original'),im.get('src')))
    out=[]; seen=set()
    for x in a:
        if not x or str(x).startswith('data:'): continue
        u=urljoin(url,str(x)); pth=urlparse(u); u=urlunparse((pth.scheme,pth.netloc,pth.path,'','','')); lo=u.lower()
        if u in seen or any(z in lo for z in ['logo','favicon','sprite','icon','captcha','yandex']): continue
        if not any(z in lo for z in ['.jpg','.jpeg','.png','.webp','.gif','/upload/','/images/']): continue
        seen.add(u); out.append(u)
    return out[:30]
def crumbs(soup):
    links=[]
    for sel in ["[class*='breadcrumb'] a","[class*='breadcrumbs'] a","nav[aria-label*='breadcrumb' i] a"]:
        links=soup.select(sel)
        if links: break
    out=[]
    for a in links:
        n=txt(a.get_text(' ',strip=True)); h=txt(a.get('href'))
        if n.lower() in {'главная','мебель','каталог','каталог товаров'} or (h and '/catalog/' not in h): continue
        if n and n not in out: out.append(n)
    return out

def parse(u):
    soup=BeautifulSoup(get(u).text,'lxml'); p=product_ld(soup); o=offer_ld(p); body=txt(soup.get_text('\n',strip=True)); ps=params(soup)
    h=soup.find('h1'); name=first(p.get('name'),h.get_text(' ',strip=True) if h else '')
    sku=first(p.get('sku'),p.get('mpn'),p.get('productID'),ps.get('Артикул'),ps.get('артикул'))
    if not sku:
        m=re.search(r'Артикул\s*:?\s*(.+?)(?=\s+Описание|\s+Характеристики|$)',body,re.I); sku=txt(m.group(1)).strip(' :;') if m else ''
    pr=price(first(o.get('price'),o.get('lowPrice')))
    if not pr:
        pt=soup.find(attrs={'itemprop':'price'}); pr=price(first(pt.get('content'),pt.get_text(' ',strip=True)) if pt else '')
    if not pr:
        m=re.search(r'(\d[\d\s\xa0]{1,12}(?:[,.]\d{1,2})?)\s*(?:руб\.?|₽)',body,re.I); pr=price(m.group(1)) if m else ''
    av=txt(o.get('availability')).lower()
    available='instock' in av or (not av and re.search(r'\bВ наличии\b',body,re.I) is not None)
    status='В наличии' if available else ('Под заказ' if ('outofstock' in av or 'preorder' in av or re.search(r'\bПод заказ\b',body,re.I)) else 'Наличие не указано')
    desc=first(p.get('description'))
    if not desc:
        for sel in ["[itemprop='description']","[class*='product'] [class*='description']","[class*='detail'] [class*='description']"]:
            t=soup.select_one(sel)
            if t and len(txt(t.get_text(' ',strip=True)))>=20: desc=txt(t.get_text(' ',strip=True)); break
    if not desc: desc=meta(soup,name='description')
    old=''
    for sel in ["[class*='old-price']","[class*='old_price']","[class*='price-old']"]:
        t=soup.select_one(sel)
        if t and price(t.get_text(' ',strip=True)): old=price(t.get_text(' ',strip=True)); break
    return {'url':u,'name':name,'sku':sku,'price':pr,'old':old,'available':available,'status':status,'description':desc,'images':images(soup,p,u),'params':ps,'cats':crumbs(soup)}

def cid(path): return str(int(hashlib.sha1(' / '.join(path).encode()).hexdigest()[:12],16))
def add(parent,tag,value,**attrs):
    v=txt(value)
    if not v: return
    e=ET.SubElement(parent,tag,attrs); e.text=v

def make(items):
    if len(items)<MIN: raise RuntimeError(f'only {len(items)} valid offers; minimum {MIN}')
    nodes={}; leaf={}
    for it in items:
        chain=tuple(it['cats'] or ['Каталог 4 Сезона']); parent=None
        for i,n in enumerate(chain):
            path=chain[:i+1]; x=cid(path); nodes[path]=(x,n,parent); parent=x
        leaf[it['url']]=parent
    root=ET.Element('yml_catalog',date=datetime.now().strftime('%Y-%m-%d %H:%M')); shop=ET.SubElement(root,'shop')
    add(shop,'name','4 Сезона'); add(shop,'company','ООО «4 сезона»'); add(shop,'url',BASE+'/')
    cur=ET.SubElement(shop,'currencies'); ET.SubElement(cur,'currency',id='RUR',rate='1')
    cats=ET.SubElement(shop,'categories')
    for path in sorted(nodes,key=lambda x:(len(x),tuple(z.lower() for z in x))):
        x,n,p=nodes[path]; a={'id':x}
        if p: a['parentId']=p
        add(cats,'category',n,**a)
    offers=ET.SubElement(shop,'offers'); used=set()
    for it in items:
        oid=it['sku'] or '4s-'+hashlib.sha1(it['url'].encode()).hexdigest()[:16]
        if oid in used: oid+='-'+hashlib.sha1(it['url'].encode()).hexdigest()[:8]
        used.add(oid); off=ET.SubElement(offers,'offer',id=oid,available=str(it['available']).lower())
        add(off,'url',it['url']); add(off,'price',it['price'])
        if it['old'] and it['old']!=it['price']: add(off,'oldprice',it['old'])
        add(off,'currencyId','RUR'); add(off,'categoryId',leaf[it['url']])
        for im in it['images']: add(off,'picture',im)
        add(off,'name',it['name']); add(off,'vendor','4 Сезона'); add(off,'vendorCode',it['sku']); add(off,'description',it['description']); add(off,'param',it['status'],name='Наличие')
        for k,v in it['params'].items():
            if k.lower() not in {'артикул','наши предложения'}: add(off,'param',v,name=k)
    ET.indent(root,space='  '); tmp=OUT.with_suffix('.yml.tmp'); ET.ElementTree(root).write(tmp,encoding='utf-8',xml_declaration=True); ET.parse(tmp); os.replace(tmp,OUT)

def main():
    urls=discover()
    if len(urls)<MIN: raise RuntimeError(f'only {len(urls)} product URLs discovered; minimum {MIN}')
    items=[]; fails=[]
    for i,u in enumerate(urls,1):
        try:
            it=parse(u)
            if not it['name'] or not it['price']: raise ValueError('missing name or price')
            items.append(it)
        except Exception as e: fails.append((u,str(e))); print('FAILED',u,e)
        if i%25==0 or i==len(urls): print(f'parsed {i}/{len(urls)} valid={len(items)} failed={len(fails)}')
    make(items); print(f'generated {OUT}: {len(items)} offers, {OUT.stat().st_size} bytes')
    if fails:
        print('non-fatal failures',len(fails))
        for u,e in fails[:20]: print('-',u,e)
if __name__=='__main__': main()
