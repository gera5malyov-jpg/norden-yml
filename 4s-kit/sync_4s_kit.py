#!/usr/bin/env python3
import json
import math
import os
import re
import time
import threading
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
MONEY=Decimal('0.01')

def s(v): return str(v or '').strip()
def norm(v): return re.sub(r'[^0-9a-zа-яё]+','',unicodedata.normalize('NFKC',s(v)).casefold())
def is_brand(v): return norm(v) == norm(BRAND)
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
        self.session=requests.Session(); self.last=0.0; self._pace_lock=threading.Lock()
    def _pace(self):
        with self._pace_lock:
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
            if page%25==0:
                print(f'KIT scan {path}: page={page}, rows={len(out)}, total={total}',flush=True)
            if not rows or (total is not None and len(out)>=total) or (total is None and len(rows)<100): break
            page+=1
        return out
    def warehouses(self): return self.all('/v1/warehouses',{'status':'ACTIVE'})
    def categories(self): return self.all('/v1/categories',{'status':['ACTIVE']})
    def characteristics(self): return self.all('/v1/characteristics',{'status':['ACTIVE']})
    def variants(self):
        first=self.request('GET','/v1/variants',params={'page':1,'per_page':100})
        rows=[x for x in self.items(first) if isinstance(x,dict)]
        total=self.total(first)
        if total is None or total<=len(rows):
            return rows
        pages=max(1,math.ceil(total/100))
        out=list(rows)
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
    def create_category(self,title,parent_id=None):
        b={'title':title}
        if parent_id: b['parent_id']=parent_id
        return self.request('POST','/v1/categories',body=b)
    def create_product(self,category_id):
        return self.request('POST','/v1/products',body={'category_ids':[str(category_id)]})
    def create_variant(self,body):
        return self.request('POST','/v1/variants',body=body)
    def patch_variant(self,variant_id,body):
        url=f'/v1/variants/{variant_id}'
        full=BASE+url
        for attempt in range(12):
            self._pace(); headers=dict(self.h); headers['Content-Type']='application/merge-patch+json'
            r=self.session.patch(full,headers=headers,json=body,timeout=120)
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
    categories={}
    offers=[]
    stack=[]
    for event,e in ET.iterparse(FEED,events=('start','end')):
        if event=='start':
            stack.append(e.tag); continue
        if e.tag=='category' and 'categories' in stack:
            cid=s(e.attrib.get('id')); title=s(e.text); parent=s(e.attrib.get('parentId'))
            if cid and title: categories[cid]={'id':cid,'title':title,'parent_id':parent or None}
            e.clear()
        elif e.tag=='offer':
            brand=s(e.findtext('vendor'))
            if not is_brand(brand):
                e.clear()
                if stack: stack.pop()
                continue
            code=s(e.findtext('vendorCode')) or s(e.attrib.get('id'))
            pr=money(e.findtext('price'))
            if code and pr:
                offers.append({
                    'id':s(e.attrib.get('id')),
                    'sku':code,
                    'name':s(e.findtext('name')) or code,
                    'description':s(e.findtext('description')),
                    'category_id':s(e.findtext('categoryId')),
                    'price':pr,
                    'brand':BRAND,
                })
            e.clear()
        if stack: stack.pop()
    if not offers: raise RuntimeError('Brand-only 4s feed is empty')
    return categories,offers

def warehouse_map(rows):
    out={}
    for title in WAREHOUSE_TITLES:
        ids=[s(x.get('id')) for x in rows if s(x.get('title'))==title and s(x.get('id'))]
        if len(ids)!=1: raise RuntimeError(f'Expected exactly one KIT warehouse {title!r}; found {len(ids)}')
        out[title]=ids[0]
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
    if not chain:
        chain=[{'id':'4s-root','title':'4 Сезона','parent_id':None}]
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

def price_row(variant_id,p):
    old=(p*Decimal('1.40')).quantize(MONEY,rounding=ROUND_HALF_UP)
    return {'variant_id':variant_id,'price':f'{old:.2f}','manual_discount_price':f'{p:.2f}'}

def desired_pricing_equal(v,p):
    pricing=v.get('pricing') or {}
    return money(pricing.get('price'))==(p*Decimal('1.40')).quantize(MONEY,rounding=ROUND_HALF_UP) and money(pricing.get('manual_discount_price'))==p

def main():
    cats,offers=parse_feed()
    kit=Kit(os.getenv('YANDEX_KIT_TOKEN',''))
    wh=warehouse_map(kit.warehouses())
    print(f'4s brand feed offers: {len(offers)}',flush=True)

    variants=kit.variants()
    kit_chars=kit.characteristics()
    code_char_ids={
        s(x.get('id')) for x in kit_chars
        if norm(x.get('title')) in {norm('Код для сайта'),norm('Артикул'),norm('Код продавца')}
        and s(x.get('id'))
    }
    site_code_char_id=next((
        s(x.get('id')) for x in kit_chars
        if norm(x.get('title'))==norm('Код для сайта') and s(x.get('id'))
    ),'')
    by_sku=defaultdict(list)
    brand_variants={}
    for v in variants:
        if not (is_brand(v.get('brand')) and s(v.get('id'))):
            continue
        vid=s(v.get('id'))
        brand_variants[vid]=v
        keys=[]
        sku=s(v.get('sku'))
        if sku: keys.append(sku)
        for ch in v.get('characteristics') or []:
            if s(ch.get('characteristic_id')) not in code_char_ids:
                continue
            vals=ch.get('values') if isinstance(ch.get('values'),list) else []
            vals=list(vals)
            if s(ch.get('value')): vals.append(s(ch.get('value')))
            keys.extend(s(x) for x in vals if s(x))
        for key in dict.fromkeys(keys):
            by_sku[key].append(v)

    print(f'KIT variants scanned: {len(variants)}; brand 4 Сезона: {len(brand_variants)}',flush=True)
    kitcats=kit.categories()

    report={
        'started_at':datetime.now(timezone.utc).isoformat(),
        'feed_offers':len(offers),'kit_variants_scanned':len(variants),
        'existing_brand_variants':len(brand_variants),
        'matched':0,'created':0,'price_changes':0,'stock_changes':0,
        'brand_patched':0,'absent_to_zero':0,'collisions':0,'errors':[],
    }
    seen_ids=set(); price_updates=[]; stock_updates=[]

    for n,o in enumerate(offers,1):
        rows=[]
        for key in dict.fromkeys([o['sku'],o['id']]):
            rows.extend(by_sku.get(key,[]))
        dedup={}
        for row in rows:
            if s(row.get('id')): dedup[s(row.get('id'))]=row
        rows=list(dedup.values())
        chosen=None
        if len(rows)==1:
            chosen=rows[0]
        elif len(rows)>1:
            b=[x for x in rows if is_brand(x.get('brand'))]
            if len(b)==1: chosen=b[0]
            else:
                report['collisions']+=1
                report['errors'].append({'sku':o['sku'],'message':f'ambiguous SKU: {len(rows)} variants'})
                continue

        if chosen is None:
            try:
                cid=ensure_category(kit,o,cats,kitcats)
                product=kit.create_product(cid); pid=s(product.get('id'))
                if not pid: raise RuntimeError('KIT did not return product id')
                p=o['price']; old=(p*Decimal('1.40')).quantize(MONEY,rounding=ROUND_HALF_UP)
                payload={
                    'sku':kit_sku(o['sku']),
                    'name':o['name'],
                    'description':o['description'],
                    'brand':BRAND,
                    'status':'PUBLISHED',
                    'product_id':pid,
                    'pricing':{'price':f'{old:.2f}','manual_discount_price':f'{p:.2f}'},
                    'stocks':[
                        {'warehouse_id':wh['СПБ'],'quantity':100,'reserved':0},
                        {'warehouse_id':wh['МСК'],'quantity':100,'reserved':0},
                    ],
                }
                if site_code_char_id:
                    payload['characteristics']=[{
                        'characteristic_id':site_code_char_id,
                        'value':o['sku'],
                        'values':[o['sku']],
                    }]
                chosen=kit.create_variant(payload)
                if not s(chosen.get('id')): raise RuntimeError('KIT did not return new variant id')
                by_sku[o['sku']]=[chosen]; brand_variants[s(chosen.get('id'))]=chosen
                report['created']+=1
            except Exception as exc:
                report['errors'].append({'sku':o['sku'],'message':str(exc)[:500]})
                continue
        else:
            report['matched']+=1

        vid=s(chosen.get('id')); seen_ids.add(vid)
        if not desired_pricing_equal(chosen,o['price']):
            price_updates.append(price_row(vid,o['price'])); report['price_changes']+=1
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

    zero=[]
    absent_ids=[]
    for vid,v in brand_variants.items():
        if vid in seen_ids: continue
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
