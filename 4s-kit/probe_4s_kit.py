#!/usr/bin/env python3
import json, os, time, unicodedata, re
import xml.etree.ElementTree as ET
from pathlib import Path
import requests

BASE='https://api.kit.yandex.net'
FEED=Path('4s-mebel.yml')
OUT=Path('4s-kit/probe_report.json')

def s(v): return str(v or '').strip()
def norm(v):
    return re.sub(r'[^0-9a-zа-яё]+','',unicodedata.normalize('NFKC',s(v)).casefold())

def feed_codes():
    codes=set(); offer_ids=set()
    for event, elem in ET.iterparse(FEED, events=('end',)):
        if elem.tag=='offer':
            oid=s(elem.attrib.get('id'))
            vc=s(elem.findtext('vendorCode'))
            if oid: offer_ids.add(oid)
            if vc: codes.add(vc)
            elem.clear()
    return codes, offer_ids

class Kit:
    def __init__(self, token):
        self.h={'Authorization':f'Bearer {token}','Accept':'application/json'}
        self.s=requests.Session(); self.last=0.0
    def get(self,path,params=None):
        for attempt in range(12):
            delay=.42-(time.monotonic()-self.last)
            if delay>0: time.sleep(delay)
            self.last=time.monotonic()
            r=self.s.get(BASE+path,headers=self.h,params=params,timeout=120)
            if r.status_code==429:
                time.sleep(float(r.headers.get('Retry-After') or min(45,5*(attempt+1)))); continue
            if r.status_code>=500:
                time.sleep(min(20,2**attempt)); continue
            r.raise_for_status()
            return r.json() if r.content else {}
        raise RuntimeError('retries exhausted')
    @staticmethod
    def items(p):
        if isinstance(p,list): return p
        if not isinstance(p,dict): return []
        for k in ('items','results','variants','warehouses'):
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
            p=self.get(path,q); rows=self.items(p); out.extend(x for x in rows if isinstance(x,dict))
            total=self.total(p)
            if not rows or (total is not None and len(out)>=total) or (total is None and len(rows)<100): break
            page+=1
        return out

def main():
    token=s(os.getenv('YANDEX_KIT_TOKEN'))
    if not token: raise SystemExit('YANDEX_KIT_TOKEN missing')
    codes, offer_ids=feed_codes()
    kit=Kit(token)
    wh=kit.all('/v1/warehouses',{'status':'ACTIVE'})
    variants=kit.all('/v1/variants')
    exact=[]; pref=[]; brand=[]; by_sku={}
    duplicates={}
    for v in variants:
        sku=s(v.get('sku')); by_sku.setdefault(sku,[]).append(v)
        if sku in codes or sku in offer_ids: exact.append(v)
        if sku.startswith('4s-') and sku[3:] in codes: pref.append(v)
        if norm(v.get('brand')) in {'4сезона','4sezona','4season','4seasons'} or '4сезон' in norm(v.get('brand')):
            brand.append(v)
    for sku, rows in by_sku.items():
        if sku and len(rows)>1 and (sku in codes or sku in offer_ids or sku.startswith('4s-')):
            duplicates[sku]=[s(x.get('id')) for x in rows]
    current_skus=set(codes)|set(offer_ids)
    brand_absent=[]
    for v in brand:
        sku=s(v.get('sku'))
        raw=sku[3:] if sku.startswith('4s-') else sku
        if sku not in current_skus and raw not in current_skus:
            brand_absent.append(v)
    def compact(v):
        return {
            'id':s(v.get('id')),'sku':s(v.get('sku')),'name':s(v.get('name')),
            'brand':s(v.get('brand')),'pricing':v.get('pricing'),
            'stocks':v.get('stocks'),
        }
    report={
        'feed_codes':len(codes),
        'feed_offer_ids':len(offer_ids),
        'kit_variants':len(variants),
        'warehouses':[{'id':s(x.get('id')),'title':s(x.get('title')),'status':s(x.get('status'))} for x in wh],
        'exact_matches':len({s(x.get('id')) for x in exact if s(x.get('id'))}),
        'prefixed_matches':len({s(x.get('id')) for x in pref if s(x.get('id'))}),
        'brand_candidates':len({s(x.get('id')) for x in brand if s(x.get('id'))}),
        'brand_absent_current_feed':len({s(x.get('id')) for x in brand_absent if s(x.get('id'))}),
        'duplicates':duplicates,
        'exact_samples':[compact(x) for x in exact[:10]],
        'prefixed_samples':[compact(x) for x in pref[:10]],
        'brand_absent_samples':[compact(x) for x in brand_absent[:10]],
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
