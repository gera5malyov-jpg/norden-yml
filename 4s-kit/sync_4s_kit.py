#!/usr/bin/env python3
import hashlib
import json
import math
import mimetypes
import os
import re
import tempfile
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlparse

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
def is_brand(v): return s(v)==BRAND
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
    def create_characteristic(self,title):
        return self.request(
            'POST','/v1/characteristics',
            body={'title':title,'type':'STRING','select_mode':'SINGLE'},
        )
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
    def upload_image_url(self,url):
        r=requests.get(url,timeout=120,headers={'User-Agent':'Mozilla/5.0'})
        r.raise_for_status()
        name=os.path.basename(urlparse(url).path) or 'image.jpg'
        mime=(r.headers.get('Content-Type') or mimetypes.guess_type(name)[0] or 'image/jpeg').split(';')[0]
        for attempt in range(12):
            self._pace()
            rr=self.session.post(
                BASE+'/v1/files',
                headers=self.h,
                files={'file':(name,r.content,mime)},
                timeout=180,
            )
            if rr.status_code==429:
                time.sleep(float(rr.headers.get('Retry-After') or min(45,5*(attempt+1)))); continue
            if rr.status_code>=500:
                time.sleep(min(20,2**attempt)); continue
            if rr.status_code>=400:
                raise HttpError(rr.status_code,rr.url,rr.text)
            return rr.json() if rr.content else {}
        raise RuntimeError('KIT image upload retries exhausted')

def parse_feed():
    categories={}; offers=[]; stack=[]
    for event,e in ET.iterparse(FEED,events=('start','end')):
        if event=='start':
            stack.append(e.tag); continue
        if e.tag=='category' and 'categories' in stack:
            cid=s(e.attrib.get('id')); title=s(e.text); parent=s(e.attrib.get('parentId'))
            if cid and title:
                categories[cid]={'id':cid,'title':title,'parent_id':parent or None}
            e.clear()
        elif e.tag=='offer':
            if s(e.findtext('vendor'))!=BRAND:
                e.clear()
                if stack: stack.pop()
                continue
            code=s(e.findtext('vendorCode')) or s(e.attrib.get('id'))
            if code:
                offers.append({
                    'id':s(e.attrib.get('id')),
                    'source_code':code,
                    'name':s(e.findtext('name')) or code,
                    'description':s(e.findtext('description')),
                    'category_id':s(e.findtext('categoryId')),
                    'price':money(e.findtext('price')),
                    'url':s(e.findtext('url')),
                    'images':list(dict.fromkeys(
                        s(x.text) for x in e.findall('picture') if s(x.text)
                    )),
                    'params':{
                        s(x.attrib.get('name')):s(x.text)
                        for x in e.findall('param')
                        if s(x.attrib.get('name'))
                        and s(x.text)
                        and norm(x.attrib.get('name')) not in {norm('Наличие'),norm('Артикул')}
                    },
                })
            e.clear()
        if stack: stack.pop()
    if len(offers)<2500:
        raise RuntimeError(f'4 Сезона feed looks incomplete: {len(offers)} offers')
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

def with_code_site(v,code_site_id,source_code):
    replace={str(code_site_id)}
    out=[
        x for x in (v.get('characteristics') or [])
        if s(x.get('characteristic_id')) not in replace
    ]
    out.append({
        'characteristic_id':code_site_id,
        'value':source_code,
        'values':[source_code],
    })
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
    return is_brand(v.get('brand'))

def characteristic_title_index(rows):
    out=defaultdict(list)
    for row in rows:
        title=s(row.get('title'))
        cid=s(row.get('id'))
        if title and cid:
            out[norm(title)].append(row)
    return out

def ensure_characteristic(kit,title,char_rows,char_index):
    key=norm(title)
    matches=char_index.get(key,[])
    if matches:
        # Prefer a STRING characteristic when duplicates happen to exist.
        string_matches=[
            x for x in matches
            if s(x.get('type')).upper() in ('','STRING') and s(x.get('id'))
        ]
        row=(string_matches or matches)[0]
        return s(row.get('id'))
    created=kit.create_characteristic(title)
    cid=s(created.get('id'))
    if not cid:
        raise RuntimeError(f'KIT did not return characteristic id for {title!r}')
    char_rows.append(created)
    char_index[key].append(created)
    return cid

def build_product_characteristics(
    kit,offer,char_rows,char_index,code_site_id,article_id,article_value
):
    out=[
        {
            'characteristic_id':code_site_id,
            'value':offer['source_code'],
            'values':[offer['source_code']],
        },
        {
            'characteristic_id':article_id,
            'value':article_value,
            'values':[article_value],
        },
    ]
    used={s(code_site_id),s(article_id)}
    for title,value in (offer.get('params') or {}).items():
        if not s(title) or not s(value):
            continue
        if norm(title) in {norm(CODE_SITE_TITLE),norm(ARTICLE_TITLE),norm('Наличие')}:
            continue
        try:
            cid=ensure_characteristic(kit,title,char_rows,char_index)
        except Exception:
            raise
        if not cid or cid in used:
            continue
        used.add(cid)
        out.append({
            'characteristic_id':cid,
            'value':s(value),
            'values':[s(value)],
        })
    return out

def generated_333_variant(v):
    kit_id=s(v.get('kit_id'))
    return bool(kit_id and s(v.get('sku'))==f'333-{kit_id}')

def prepare_media(kit,offer,report):
    urls=list(dict.fromkeys(x for x in (offer.get('images') or []) if s(x)))[:10]
    media=[]
    if not urls:
        report['new_without_source_images']+=1
        return media
    for url in urls:
        try:
            uploaded=kit.upload_image_url(url)
            image_id=s(uploaded.get('id'))
            if not image_id:
                raise RuntimeError('KIT did not return image id')
            media.append({
                'type':'IMAGE',
                'display_sequence':len(media),
                'image_id':image_id,
            })
            report['images_uploaded']+=1
        except Exception as exc:
            report['image_failures']+=1
            if len(report['image_errors'])<200:
                report['image_errors'].append({
                    'source_code':offer.get('source_code'),
                    'url':url,
                    'message':str(exc)[:500],
                })
    return media

def dedupe_feed_offers(offers):
    grouped=defaultdict(list)
    for offer in offers:
        key=(norm(offer.get('source_code')),norm(offer.get('name')))
        grouped[key].append(offer)
    unique=[]
    duplicate_identities={}
    price_conflicts={}
    for key,rows in grouped.items():
        rows=sorted(rows,key=lambda x:(x.get('url') or '',x.get('id') or ''))
        unique.append(rows[0])
        if len(rows)>1:
            label=f"{rows[0].get('source_code')} | {rows[0].get('name')}"
            duplicate_identities[label]=len(rows)
            prices=sorted({
                str(x['price']) for x in rows if x.get('price') is not None
            })
            if len(prices)>1:
                price_conflicts[label]=prices
    return unique,duplicate_identities,price_conflicts

def add_index(index,key,variant):
    nk=norm(key)
    if nk:
        index[nk].append(variant)

def match_offer(offer,by_code_site,by_article,by_name):
    scores={}
    reasons=defaultdict(set)

    def add(rows,points,reason):
        for row in rows:
            vid=s(row.get('id'))
            if not vid or reason in reasons[vid]:
                continue
            scores[vid]=scores.get(vid,0)+points
            reasons[vid].add(reason)

    source=norm(offer.get('source_code'))
    name=norm(offer.get('name'))
    if source:
        add(by_code_site.get(source,[]),100,'code_site')
        add(by_article.get(source,[]),60,'article')
    if name:
        add(by_name.get(name,[]),40,'name')

    if not scores:
        return [],False,{}

    best_score=max(scores.values())
    best_ids=[vid for vid,score in scores.items() if score==best_score]
    all_rows={}
    for rows in (by_code_site.get(source,[]),by_article.get(source,[]),by_name.get(name,[])):
        for row in rows:
            if s(row.get('id')):
                all_rows[s(row.get('id'))]=row
    best=[all_rows[vid] for vid in best_ids if vid in all_rows]

    # One best card is unambiguous. If several cards share an exact supplier
    # article/code, treat them all as existing matches rather than creating a duplicate.
    strong=best_score>=60
    ambiguous=len(best)>1 and not strong
    return best,ambiguous,{
        'score':best_score,
        'reasons':{vid:sorted(reasons[vid]) for vid in best_ids},
    }

def main():
    existing_only=s(os.getenv('FOURS_EXISTING_ONLY')).casefold() in {'1','true','yes','y'}
    cats,raw_offers=parse_feed()
    offers,feed_duplicates,feed_price_conflicts=dedupe_feed_offers(raw_offers)
    kit=Kit(os.getenv('YANDEX_KIT_TOKEN',''))
    wh=warehouse_map(kit.warehouses())
    char_rows=kit.characteristics()
    code_site_id,article_id=resolve_special_characteristics(char_rows)
    char_index=characteristic_title_index(char_rows)
    print(f'4 Сезона feed offers: raw={len(raw_offers)} unique_codes={len(offers)} duplicates={len(feed_duplicates)}',flush=True)

    variants=kit.variants()
    by_code_site=defaultdict(list)
    by_article=defaultdict(list)
    by_name=defaultdict(list)
    managed={}
    for v in variants:
        vid=s(v.get('id'))
        if not vid or not is_brand(v.get('brand')):
            continue
        managed[vid]=v
        add_index(by_code_site,char_value(v,code_site_id),v)
        add_index(by_article,char_value(v,article_id),v)
        add_index(by_article,s(v.get('sku')),v)
        add_index(by_name,s(v.get('name')),v)

    print(f'KIT variants scanned: {len(variants)}; managed 4 Сезона: {len(managed)}',flush=True)
    kitcats=kit.categories()

    report={
        'started_at':datetime.now(timezone.utc).isoformat(),
        'existing_only':existing_only,
        'feed_offers_raw':len(raw_offers),'feed_unique_source_codes':len(offers),
        'feed_duplicate_article_name_identities':len(feed_duplicates),
        'feed_price_conflicts':len(feed_price_conflicts),
        'kit_variants_scanned':len(variants),
        'existing_managed_variants':len(managed),
        'matched':0,'matched_variants':0,'matched_by_code_site':0,
        'matched_by_article':0,'matched_by_name':0,'ambiguous_name_matches':0,
        'created':0,'skipped_new_existing_only':0,
        'price_changes':0,'stock_changes':0,
        'images_uploaded':0,'image_repairs':0,'image_failures':0,
        'new_without_source_images':0,'image_errors':[],
        'characteristics_written':0,'characteristic_repairs':0,
        'description_repairs':0,
        'brand_patched':0,'sku_fixed_to_333_kit_id':0,'code_site_filled':0,
        'absent_to_zero':0,'collisions':0,'errors':[],
    }
    seen_ids=set()
    price_updates={}
    stock_updates={}
    report['feed_price_conflict_details']={
        key:prices for key,prices in list(feed_price_conflicts.items())[:200]
    }

    for n,o in enumerate(offers,1):
        source=o['source_code']
        rows,ambiguous,match_meta=match_offer(
            o,by_code_site,by_article,by_name
        )
        chosen=None
        extra_rows=[]
        created_now=False

        if rows and ambiguous:
            # Same normalized name points to more than one card but no exact article/code
            # distinguishes them. Treat them as existing so we do not create duplicates,
            # but do not overwrite their price or Код для сайта blindly.
            report['ambiguous_name_matches']+=1
            report['collisions']+=1
            for candidate in rows:
                vid=s(candidate.get('id'))
                if vid:
                    seen_ids.add(vid)
            if len(report['errors'])<300:
                report['errors'].append({
                    'source_code':source,
                    'name':o.get('name'),
                    'message':'ambiguous name-only match; no new product created',
                })
            continue

        if rows:
            chosen=rows[0]
            extra_rows=rows[1:]
            report['matched']+=1
            reason_set=set()
            for reasons in (match_meta.get('reasons') or {}).values():
                reason_set.update(reasons)
            if 'code_site' in reason_set:
                report['matched_by_code_site']+=1
            elif 'article' in reason_set:
                report['matched_by_article']+=1
            elif 'name' in reason_set:
                report['matched_by_name']+=1

        if chosen is None:
            if existing_only:
                report['skipped_new_existing_only']+=1
                continue
            if o['price'] is None:
                report['errors'].append({'source_code':source,'message':'new product has no valid price; creation skipped'})
                continue
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
                media=prepare_media(kit,o,report)
                if media:
                    payload['media']=media
                created=kit.create_variant(payload)
                vid=s(created.get('id'))
                if not vid: raise RuntimeError('KIT did not return new variant id')
                kit_id=created.get('kit_id')
                if kit_id in (None,''):
                    created=kit.get_variant(vid); kit_id=created.get('kit_id')
                final_sku=final_sku_from_kit_id(kit_id)
                chars=build_product_characteristics(
                    kit,o,char_rows,char_index,
                    code_site_id,article_id,final_sku,
                )
                patch_new={
                    'sku':final_sku,
                    'brand':BRAND,
                    'characteristics':chars,
                }
                if o.get('description'):
                    patch_new['description']=o['description']
                kit.patch_variant(vid,patch_new)
                report['characteristics_written']+=max(0,len(chars)-2)
                chosen=kit.get_variant(vid)
                managed[vid]=chosen
                add_index(by_code_site,source,chosen)
                add_index(by_article,final_sku,chosen)
                add_index(by_name,o.get('name'),chosen)
                report['created']+=1
                report['sku_fixed_to_333_kit_id']+=1
                report['code_site_filled']+=1
                created_now=True
            except Exception as exc:
                report['errors'].append({'source_code':source,'message':str(exc)[:500]})
                continue
        targets=[chosen]+extra_rows if chosen is not None else []
        report['matched_variants']+=len(targets) if not created_now else 0

        for target in targets:
            vid=s(target.get('id'))
            if not vid:
                continue
            seen_ids.add(vid)

            # Once an existing card is matched by name/article/code, persist the
            # supplier article in "Код для сайта" for deterministic future runs.
            if not created_now and char_value(target,code_site_id)!=source:
                try:
                    chars=with_code_site(target,code_site_id,source)
                    kit.patch_variant(vid,{'characteristics':chars})
                    target=dict(target)
                    target['characteristics']=chars
                    report['code_site_filled']+=1
                    add_index(by_code_site,source,target)
                except Exception as exc:
                    if len(report['errors'])<300:
                        report['errors'].append({
                            'source_code':source,
                            'message':f'code_site patch failed: {str(exc)[:400]}',
                        })

            # Repair content on cards created by this integration. This
            # intentionally does not overwrite legacy/non-333 cards.
            if not created_now and generated_333_variant(target):
                repair={}
                if o.get('description') and s(target.get('description'))!=s(o.get('description')):
                    repair['description']=o['description']
                try:
                    final_article=f"333-{s(target.get('kit_id'))}"
                    desired_chars=build_product_characteristics(
                        kit,o,char_rows,char_index,
                        code_site_id,article_id,final_article,
                    )
                    current_pairs={
                        (s(x.get('characteristic_id')),s(x.get('value')) or (
                            s((x.get('values') or [''])[0]) if x.get('values') else ''
                        ))
                        for x in (target.get('characteristics') or [])
                    }
                    desired_pairs={
                        (s(x.get('characteristic_id')),s(x.get('value')))
                        for x in desired_chars
                    }
                    if not desired_pairs.issubset(current_pairs):
                        repair['characteristics']=desired_chars
                except Exception as exc:
                    if len(report['errors'])<300:
                        report['errors'].append({
                            'source_code':source,
                            'message':f'build characteristics failed: {str(exc)[:400]}',
                        })
                if repair:
                    try:
                        kit.patch_variant(vid,repair)
                        if 'description' in repair:
                            report['description_repairs']+=1
                            target['description']=repair['description']
                        if 'characteristics' in repair:
                            report['characteristic_repairs']+=1
                            report['characteristics_written']+=max(
                                0,len(repair['characteristics'])-2
                            )
                            target['characteristics']=repair['characteristics']
                    except Exception as exc:
                        if len(report['errors'])<300:
                            report['errors'].append({
                                'source_code':source,
                                'message':f'content repair failed: {str(exc)[:400]}',
                            })

            target_offer=o
            target_price=o.get('price')
            identity_label=f"{source} | {o.get('name')}"
            if identity_label in feed_price_conflicts:
                target_price=None
                if len(report['errors'])<300:
                    report['errors'].append({
                        'source_code':source,
                        'name':o.get('name'),
                        'message':'same supplier article+name has conflicting prices; price unchanged',
                    })
            if (
                not created_now
                and target_price is not None
                and not pricing_equal(target,target_price)
            ):
                price_updates[vid]=desired_price_row(vid,target_price)

            if not created_now:
                for title in WAREHOUSE_TITLES:
                    wid=wh[title]
                    if current_stock(target,wid)!=100:
                        stock_updates[(vid,wid)]={
                            'variant_id':vid,'warehouse_id':wid,'quantity':100
                        }

            # Repair images only on cards generated by this integration.
            if (
                generated_333_variant(target)
                and not (target.get('media') or [])
                and o.get('images')
            ):
                media=prepare_media(kit,o,report)
                if media:
                    try:
                        kit.patch_variant(vid,{'media':media})
                        report['image_repairs']+=1
                        target['media']=media
                    except Exception as exc:
                        report['image_failures']+=1
                        if len(report['image_errors'])<200:
                            report['image_errors'].append({
                                'source_code':source,
                                'url':'<patch media>',
                                'message':str(exc)[:500],
                            })

        if len(price_updates)>=500:
            batch=list(price_updates.values())
            kit.bulk_prices(batch)
            report['price_changes']+=len(batch)
            price_updates.clear()
        if len(stock_updates)>=500:
            batch=list(stock_updates.values())
            kit.bulk_stocks(batch)
            report['stock_changes']+=len(batch)
            stock_updates.clear()

        if n%50==0:
            print(
                f'synced {n}/{len(offers)} matched_codes={report["matched"]} '
                f'matched_variants={report["matched_variants"]} created={report["created"]}',
                flush=True,
            )

    if price_updates:
        batch=list(price_updates.values())
        kit.bulk_prices(batch)
        report['price_changes']+=len(batch)
    if stock_updates:
        batch=list(stock_updates.values())
        kit.bulk_stocks(batch)
        report['stock_changes']+=len(batch)

    zero={}
    absent_ids=[]
    for vid,v in managed.items():
        if vid in seen_ids:
            continue
        absent_ids.append(vid)
        for title in WAREHOUSE_TITLES:
            wid=wh[title]
            if current_stock(v,wid)!=0:
                zero[(vid,wid)]={'variant_id':vid,'warehouse_id':wid,'quantity':0}
    if zero:
        kit.bulk_stocks(list(zero.values()))

    report['absent_to_zero']=len(absent_ids)
    report['absent_stock_updates']=len(zero)
    report['finished_at']=datetime.now(timezone.utc).isoformat()
    report['status']='ok' if not report['errors'] else 'degraded'
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':
    main()
