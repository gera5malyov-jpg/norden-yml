#!/usr/bin/env python3
import hashlib
import json
import math
import os
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import requests

BASE='https://api.kit.yandex.net'
FEED=Path('4s-mebel.yml')
REPORT=Path('4s-kit/last_sync_report.json')
BRAND='4 Сезона'
WAREHOUSE_TITLES=('СПБ','МСК')
CODE_SITE_TITLE='Код для сайта'
ARTICLE_TITLE='Артикул'
MONEY=Decimal('0.01')

def s(v): return str(v or '').strip()
def norm(v): return re.sub(r'[^0-9a-zа-яё]+','',unicodedata.normalize('NFKC',s(v)).casefold())
def is_brand(v): return norm(v) in {'4сезона','4sezona'}
def money(v):
    try:
        d=Decimal(str(v).replace(' ','').replace(',','.'))
    except (InvalidOperation,ValueError,TypeError):
        return None
    if d<=0: return None
    return d.quantize(MONEY,rounding=ROUND_HALF_UP)

class HttpError(RuntimeError):
    def __init__(self,status,url,body):
        super().__init__(f'HTTP {status} {url}: {body[:600]}')
        self.status=status

class Kit:
    def __init__(self,token):
        token=s(token)
        if not token: raise RuntimeError('YANDEX_KIT_TOKEN is not configured')
        self.h={'Authorization':f'Bearer {token}','Accept':'application/json'}
        self.session=requests.Session(); self.last=0.0; self._lock=threading.Lock()
    def _pace(self):
        with self._lock:
            delay=.42-(time.monotonic()-self.last)
            if delay>0: time.sleep(delay)
            self.last=time.monotonic()
    def request(self,method,path,params=None,body=None):
        url=BASE+path
        for attempt in range(12):
            self._pace()
            headers=dict(self.h)
            if body is not None: headers['Content-Type']='application/json'
            r=self.session.request(method,url,headers=headers,params=params,json=body,timeout=120)
            if r.status_code==429:
                time.sleep(float(r.headers.get('Retry-After') or min(45,5*(attempt+1)))); continue
            if r.status_code>=500:
                time.sleep(min(20,2**attempt)); continue
            if r.status_code>=400: raise HttpError(r.status_code,r.url,r.text)
            if not r.content: return {}
            try: return r.json()
            except Exception: return {'raw':r.text}
        raise RuntimeError(f'KIT retries exhausted: {method} {path}')
    @staticmethod
    def items(p):
        if isinstance(p,list): return p
        if not isinstance(p,dict): return []
        for k in ('items','results','variants','warehouses','categories','characteristics','products'):
            if isinstance(p.get(k),list): return p[k]
        d=p.get('data')
        if isinstance(d,list): return d
        if isinstance(d,dict) and isinstance(d.get('items'),list): return d['items']
        return []
    @staticmethod
    def total(p):
        if not isinstance(p,dict): return None
        for k in ('total','total_count'):
            if isinstance(p.get(k),int): return p[k]
        m=p.get('meta')
        if isinstance(m,dict):
            for k in ('total','total_count'):
                if isinstance(m.get(k),int): return m[k]
        return None
    def all(self,path,params=None):
        out=[]; page=1
        while True:
            q=dict(params or {}); q.update({'page':page,'per_page':100})
            p=self.request('GET',path,params=q); rows=self.items(p)
            out.extend(x for x in rows if isinstance(x,dict))
            total=self.total(p)
            if not rows or (total is not None and len(out)>=total) or (total is None and len(rows)<100): break
            page+=1
        return out
    def warehouses(self): return self.all('/v1/warehouses',{'status':'ACTIVE'})
    def categories(self): return self.all('/v1/categories',{'status':['ACTIVE']})
    def characteristics(self): return self.all('/v1/characteristics',{'status':['ACTIVE']})
    def variants(self):
        first=self.request('GET','/v1/variants',params={'page':1,'per_page':100})
        out=[x for x in self.items(first) if isinstance(x,dict)]
        total=self.total(first)
        if total is None or total<=len(out): return out
        pages=max(1,math.ceil(total/100))
        def fetch(page):
            p=self.request('GET','/v1/variants',params={'page':page,'per_page':100})
            return page,[x for x in self.items(p) if isinstance(x,dict)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs=[pool.submit(fetch,p) for p in range(2,pages+1)]
            done=1
            for fut in as_completed(futs):
                page,batch=fut.result(); out.extend(batch); done+=1
                if done%25==0 or done==pages:
                    print(f'KIT variants scan: {done}/{pages} pages, rows={len(out)}',flush=True)
        return out
    def get_variant(self,variant_id): return self.request('GET',f'/v1/variants/{variant_id}')
    def create_category(self,title,parent_id=None):
        b={'title':title}
        if parent_id: b['parent_id']=parent_id
        return self.request('POST','/v1/categories',body=b)
    def create_product(self,category_id): return self.request('POST','/v1/products',body={'category_ids':[str(category_id)]})
    def create_variant(self,body): return self.request('POST','/v1/variants',body=body)
    def patch_variant(self,variant_id,body):
        full=BASE+f'/v1/variants/{variant_id}'
        for attempt in range(12):
            self._pace(); h=dict(self.h); h['Content-Type']='application/merge-patch+json'
            r=self.session.patch(full,headers=h,json=body,timeout=120)
            if r.status_code==429:
                time.sleep(float(r.headers.get('Retry-After') or min(45,5*(attempt+1)))); continue
            if r.status_code>=500:
                time.sleep(min(20,2**attempt)); continue
            if r.status_code>=400: raise HttpError(r.status_code,r.url,r.text)
            return r.json() if r.content else {}
        raise RuntimeError('KIT patch retries exhausted')
    def bulk_prices(self,items):
        for i in range(0,len(items),5000):
            self.request('POST','/v1/variants/prices/bulk_update',body={'items':items[i:i+5000]})
    def bulk_stocks(self,items):
        for i in range(0,len(items),5000):
            self.request('POST','/v1/variants/stocks/bulk_update',body={'items':items[i:i+5000]})

def parse_feed():
    categories={}; offers=[]; stack=[]
    for event,e in ET.iterparse(FEED,events=('start','end')):
        if event=='start':
            stack.append(e.tag); continue
        if e.tag=='category' and 'categories' in stack:
            cid=s(e.attrib.get('id')); title=s(e.text); parent=s(e.attrib.get('parentId'))
            if cid and title: categories[cid]={'id':cid,'title':title,'parent_id':parent or None}
            e.clear()
        elif e.tag=='offer':
            code=s(e.findtext('vendorCode')) or s(e.attrib.get('id'))
            if not code.casefold().startswith('4s-'):
                e.clear()
                if stack: stack.pop()
                continue
            pr=money(e.findtext('price'))
            if code and pr:
                offers.append({
                    'id':s(e.attrib.get('id')),'source_code':code,
                    'name':s(e.findtext('name')) or code,
                    'description':s(e.findtext('description')),
                    'category_id':s(e.findtext('categoryId')),
                    'price':pr,
                })
            e.clear()
        if stack: stack.pop()
    if not offers: raise RuntimeError('4 Сезона feed is empty')
    return categories,offers

def warehouse_map(rows):
    out={}
    for title in WAREHOUSE_TITLES:
        ids=[s(x.get('id')) for x in rows if s(x.get('title'))==title and s(x.get('id'))]
        if len(ids)!=1: raise RuntimeError(f'Expected exactly one KIT warehouse {title!r}; found {len(ids)}')
        out[title]=ids[0]
    return out

def resolve_special_characteristics(rows):
    by_title=defaultdict(list)
    for row in rows:
        by_title[norm(row.get('title'))].append(row)
    def one(title):
        matches=by_title[norm(title)]
        if len(matches)!=1: raise RuntimeError(f'Expected exactly one characteristic {title!r}; found {len(matches)}')
        cid=s(matches[0].get('id'))
        if not cid: raise RuntimeError(f'Characteristic {title!r} has no id')
        return cid
    return one(CODE_SITE_TITLE),one(ARTICLE_TITLE)

def char_value(v,cid):
    for x in v.get('characteristics') or []:
        if s(x.get('characteristic_id'))==s(cid):
            return s(x.get('value')) or (s((x.get('values') or [''])[0]) if x.get('values') else '')
    return ''

def merged_special_chars(v,code_site_id,article_id,source_code,final_article):
    replace={str(code_site_id),str(article_id)}
    out=[x for x in (v.get('characteristics') or []) if s(x.get('characteristic_id')) not in replace]
    out.extend([
        {'characteristic_id':code_site_id,'value':source_code,'values':[source_code]},
        {'characteristic_id':article_id,'value':final_article,'values':[final_article]},
    ])
    return out

def current_stock(v,wid):
    for x in v.get('stocks') or []:
        if s(x.get('warehouse_id'))==s(wid):
            try: return int(x.get('quantity') or 0)
            except Exception: return None
    return 0

def cat_chain(cid,cats):
    out=[]; seen=set(); cur=s(cid)
    while cur and cur not in seen and cur in cats:
        seen.add(cur); out.append(cats[cur]); cur=s(cats[cur].get('parent_id'))
    out.reverse(); return out

def ensure_category(kit,offer,cats,kitcats):
    chain=cat_chain(offer['category_id'],cats)
    if not chain: chain=[{'id':'4s-root','title':'4 Сезона','parent_id':None}]
    parent=''
    for src in chain:
        title=s(src.get('title'))
        matches=[x for x in kitcats if norm(x.get('title'))==norm(title) and s(x.get('parent_id'))==parent]
        if len(matches)>1: raise RuntimeError(f'Ambiguous KIT category {title!r}')
        if matches:
            cid=s(matches[0].get('id'))
        else:
            created=kit.create_category(title,parent or None); cid=s(created.get('id'))
            if not cid: raise RuntimeError(f'KIT did not return category id for {title!r}')
            row=dict(created); row.setdefault('parent_id',parent); kitcats.append(row)
        parent=cid
    return parent

def desired_price_row(variant_id,p):
    old=(p*Decimal('1.40')).quantize(MONEY,rounding=ROUND_HALF_UP)
    return {'variant_id':variant_id,'price':f'{old:.2f}','manual_discount_price':f'{p:.2f}'}

def pricing_equal(v,p):
    pricing=v.get('pricing') or {}
    old=(p*Decimal('1.40')).quantize(MONEY,rounding=ROUND_HALF_UP)
    return money(pricing.get('price'))==old and money(pricing.get('manual_discount_price'))==p

def final_sku_from_kit_id(kit_id):
    if kit_id in (None,''): raise RuntimeError('KIT did not return kit_id')
    return f'333-{kit_id}'

def is_managed_4s_variant(v,code_site_id):
    code=char_value(v,code_site_id)
    sku=s(v.get('sku'))
    if code.casefold().startswith('4s-'): return True
    if sku.casefold().startswith('4s-'): return True
    if sku.casefold().startswith('333-4s-'): return True
    if is_brand(v.get('brand')) and sku.startswith('333-') and s(v.get('kit_id')) and sku==f"333-{v.get('kit_id')}":
        return True
    return False

def main():
    cats,offers=parse_feed()
    kit=Kit(os.getenv('YANDEX_KIT_TOKEN',''))
    wh=warehouse_map(kit.warehouses())
    code_site_id,article_id=resolve_special_characteristics(kit.characteristics())
    print(f'4 Сезона feed offers: {len(offers)}',flush=True)

    variants=kit.variants()
    by_source=defaultdict(list)
    managed={}
    for v in variants:
        vid=s(v.get('id'))
        if not vid: continue
        source=char_value(v,code_site_id)
        sku=s(v.get('sku'))
        if source.casefold().startswith('4s-'):
            by_source[source].append(v)
        if sku.casefold().startswith('4s-'):
            by_source[sku].append(v)
        if sku.casefold().startswith('333-4s-'):
            by_source[sku[4:]].append(v)
        if is_managed_4s_variant(v,code_site_id):
            managed[vid]=v

    print(f'KIT variants scanned: {len(variants)}; managed 4 Сезона: {len(managed)}',flush=True)
    kitcats=kit.categories()

    report={
        'started_at':datetime.now(timezone.utc).isoformat(),
        'feed_offers':len(offers),'kit_variants_scanned':len(variants),
        'existing_managed_variants':len(managed),
        'matched':0,'created':0,'price_changes':0,'stock_changes':0,
        'brand_patched':0,'sku_fixed_to_333_kit_id':0,'code_site_filled':0,
        'absent_to_zero':0,'collisions':0,'errors':[],
    }
    seen_ids=set(); price_updates=[]; stock_updates=[]

    for n,o in enumerate(offers,1):
        source=o['source_code']
        unique={}
        for row in by_source.get(source,[]):
            if s(row.get('id')): unique[s(row.get('id'))]=row
        rows=list(unique.values())
        chosen=None
        if len(rows)==1:
            chosen=rows[0]
        elif len(rows)>1:
            report['collisions']+=1
            report['errors'].append({'source_code':source,'message':f'ambiguous source mapping: {len(rows)} variants'})
            continue

        if chosen is None:
            try:
                cid=ensure_category(kit,o,cats,kitcats)
                product=kit.create_product(cid); pid=s(product.get('id'))
                if not pid: raise RuntimeError('KIT did not return product id')
                temp='4S-TMP-'+hashlib.sha1(source.encode('utf-8')).hexdigest()[:16]
                p=o['price']; old=(p*Decimal('1.40')).quantize(MONEY,rounding=ROUND_HALF_UP)
                payload={
                    'sku':temp,'name':o['name'],'description':o['description'],
                    'brand':BRAND,'status':'PUBLISHED','product_id':pid,
                    'pricing':{'price':f'{old:.2f}','manual_discount_price':f'{p:.2f}'},
                    'stocks':[
                        {'warehouse_id':wh['СПБ'],'quantity':100,'reserved':0},
                        {'warehouse_id':wh['МСК'],'quantity':100,'reserved':0},
                    ],
                }
                created=kit.create_variant(payload)
                vid=s(created.get('id'))
                if not vid: raise RuntimeError('KIT did not return new variant id')
                kit_id=created.get('kit_id')
                if kit_id in (None,''):
                    created=kit.get_variant(vid); kit_id=created.get('kit_id')
                final_sku=final_sku_from_kit_id(kit_id)
                chars=[
                    {'characteristic_id':code_site_id,'value':source,'values':[source]},
                    {'characteristic_id':article_id,'value':final_sku,'values':[final_sku]},
                ]
                kit.patch_variant(vid,{'sku':final_sku,'brand':BRAND,'characteristics':chars})
                chosen=kit.get_variant(vid)
                by_source[source]=[chosen]; managed[vid]=chosen
                report['created']+=1
                report['sku_fixed_to_333_kit_id']+=1
                report['code_site_filled']+=1
            except Exception as exc:
                report['errors'].append({'source_code':source,'message':str(exc)[:500]})
                continue
        else:
            report['matched']+=1
            vid=s(chosen.get('id'))
            kit_id=chosen.get('kit_id')
            if kit_id in (None,''):
                try:
                    chosen=kit.get_variant(vid); kit_id=chosen.get('kit_id')
                except Exception as exc:
                    report['errors'].append({'source_code':source,'message':f'get kit_id: {str(exc)[:400]}'})
                    continue
            final_sku=final_sku_from_kit_id(kit_id)
            patch={}
            if s(chosen.get('sku'))!=final_sku:
                patch['sku']=final_sku
            if not is_brand(chosen.get('brand')):
                patch['brand']=BRAND
            current_code=char_value(chosen,code_site_id)
            current_article=char_value(chosen,article_id)
            if current_code!=source or current_article!=final_sku:
                patch['characteristics']=merged_special_chars(chosen,code_site_id,article_id,source,final_sku)
            if patch:
                try:
                    kit.patch_variant(vid,patch)
                    if 'sku' in patch: report['sku_fixed_to_333_kit_id']+=1
                    if 'brand' in patch: report['brand_patched']+=1
                    if 'characteristics' in patch and current_code!=source: report['code_site_filled']+=1
                    chosen=kit.get_variant(vid)
                except Exception as exc:
                    report['errors'].append({'source_code':source,'message':f'patch existing: {str(exc)[:400]}'})
                    continue
            managed[vid]=chosen

        vid=s(chosen.get('id')); seen_ids.add(vid)
        if not pricing_equal(chosen,o['price']):
            price_updates.append(desired_price_row(vid,o['price'])); report['price_changes']+=1
        for title in WAREHOUSE_TITLES:
            wid=wh[title]
            if current_stock(chosen,wid)!=100:
                stock_updates.append({'variant_id':vid,'warehouse_id':wid,'quantity':100}); report['stock_changes']+=1
        if len(price_updates)>=500:
            kit.bulk_prices(price_updates); price_updates.clear()
        if len(stock_updates)>=500:
            kit.bulk_stocks(stock_updates); stock_updates.clear()
        if n%50==0:
            print(f'synced {n}/{len(offers)} matched={report["matched"]} created={report["created"]}',flush=True)

    if price_updates: kit.bulk_prices(price_updates)
    if stock_updates: kit.bulk_stocks(stock_updates)

    zero=[]; absent_ids=[]
    for vid,v in managed.items():
        if vid in seen_ids: continue
        source=char_value(v,code_site_id)
        sku=s(v.get('sku'))
        # Zero only variants clearly managed by this 4s integration.
        if not (source.casefold().startswith('4s-') or sku.casefold().startswith('4s-') or sku.casefold().startswith('333-4s-')):
            continue
        absent_ids.append(vid)
        for title in WAREHOUSE_TITLES:
            wid=wh[title]
            if current_stock(v,wid)!=0:
                zero.append({'variant_id':vid,'warehouse_id':wid,'quantity':0})
    if zero: kit.bulk_stocks(zero)
    report['absent_to_zero']=len(absent_ids)
    report['absent_stock_updates']=len(zero)
    report['finished_at']=datetime.now(timezone.utc).isoformat()
    report['status']='ok' if not report['errors'] else 'degraded'
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':
    main()
