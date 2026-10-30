#!/usr/bin/env python3
"""Mass-fill Ozon TNVED only where classification can be grounded and Ozon accepts code."""
import os
import sys
import re
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, 'ozon')
import tnved_sync as o

def norm(s):
    return ' '.join(str(s or '').upper().replace('Ё', 'Е').split())

def vals(p):
    out = {}
    for a in p.get('attributes') or []:
        for x in a.get('values') or []:
            if x.get('value'):
                out[int(a.get('id') or 0)] = str(x['value'])
    return out

def classify(p, attrnames):
    g = (int(p.get('description_category_id') or 0), int(p.get('type_id') or 0))
    t = norm(p.get('name'))
    a = {}
    for el in p.get('attributes') or []:
        idn = int(el.get('id') or 0)
        name = attrnames.get(g, {}).get(idn, '')
        if name:
            v = [str(x.get('value') or '') for x in el.get('values') or []]
            a[name] = '; '.join(x for x in v if x)
    body = a.get('Материал корпуса', '')
    cover = a.get('Материал обивки', '')
    top = a.get('Материал столешницы', '')
    base = a.get('Материал подстолья', '')
    wood = ('ЛДСП', 'МДФ', 'Дерево', 'Массив дерева', 'Массив березы', 'Фанера', 'Массив сосны', 'Массив бука', 'Шпон')
    material = (body,cover,top,base)
    if g == (17027916,95044):
        # Freeform textile-filled / frameless pouffes without documented wood or metal support
        if body == 'Текстиль' and cover not in ('', 'Без обивки'):
            return '9401800009', 'pouf: textile body and cover, other seating'
        if body in wood and cover and cover != 'Без обивки':
            return '9401610000', 'pouf: wooden upholstered frame'
        if body == 'Металл' and cover and cover != 'Без обивки':
            return '9401710009', 'pouf: upholstered metal frame'
        if body == 'Пластик' and cover == 'Без обивки':
            return '9401800009', 'pouf: plastic seating'
    if g == (17027917,94995) and 'ИНСТАЙЛ КР-04' in t and 'КРОВАТЬ' in t:
        return '9403500009', 'BTS official producer: LDSP bedroom bed'
    if g == (17027918,95005) and base == 'Металл' and top == 'Стекло':
        try: h = float(a.get('Высота, см','0').replace(',','.'))
        except: h=0
        if h>80: return '9403109800','metal framed computer desk over 80 cm'
        if 0<h<=80: return '9403105100','metal framed office desk up to 80 cm'
    if g == (17027918,95002) and base == 'Металл':
        return '9403208009','metal framed coffee table'
    if g == (17027918,95015) and base == 'Металл':
        return '9403208009','metal framed dining table'
    if g in ((17027916,95048),(17027916,95023),(17027916,95053)):
        if body == 'Металл' and cover and cover != 'Без обивки' and not re.search(r'ГАЗЛИФТ|ПОДЪЕМ|РЕГУЛИРУ', t):
            return '9401710009','upholstered fixed metal chair'
        if body in wood and cover and cover != 'Без обивки':
            return '9401610000','upholstered fixed wooden chair'
        if body == 'Металл' and cover == 'Без обивки':
            return '9401790009','unupholstered metal chair'
        if body in wood and cover == 'Без обивки':
            return '9401690000','unupholstered wooden chair'
        if body == 'Пластик' and cover == 'Без обивки':
            return '9401800009','plastic chair'
    if g in ((80375615,95030),(80375615,95035)):
        if body in wood and cover and cover != 'Без обивки' and not any(w in t for w in ['РОТАНГ','РАТТАН','БАМБУК']):
            return '9401610000','upholstered wooden armchair'
        if body == 'Металл' and cover and cover != 'Без обивки':
            return '9401710009','upholstered metal armchair'
    if g == (80731485,95098):
        if body == 'Металл':
            return '9403208009','metal shelving'
        if body in wood and re.search('СТЕЛЛАЖ|ЭТАЖЕРКА|ПОЛКА',t):
            return '9403609009','wooden household shelving'
    if g in ((17027918,95002),(17027918,95015),(17027918,95101)):
        if top in wood and base in wood:
            return '9403601009','wooden dining or living furniture'
    if g == (17027919,95108):
        if body in wood and re.search('КНИЖН|ВИТРИН|ПОСУД|ГОСТИН',t):
            return '9403601009','wooden living room cabinet'
        if body in wood and re.search('СПАЛЬН|ПЛАТЯН|ОДЕЖД|ГАРДЕРОБ|КУПЕ',t):
            return '9403500009','wooden wardrobe'
        if body == 'Металл' and re.search('ШКАФ',t):
            return '9403208009','other metal cabinet'
    if g == (80770870,95092) and body in wood:
        return '9403601009','wooden living-room unit'
    if g == (17028726,96029) and 'ШИРМА' in t:
        if any(x in t for x in ('ДЕРЕВЯННАЯ','ДЕРЕВО','МДФ','ЛДСП')) and not 'БАМБУК' in t:
            return '9403609009','wooden folding screen'
        # Bamboo 9403820000 exists in TNVED but not in Ozon dictionary for this category
    return None, 'unknown or insufficient material/construction'

def main():
    catalog=o.list_products()
    catalog_idx={str(x.get('offer_id')):int(x['product_id']) for x in catalog if x.get('product_id')}
    cards=o.get_product_attributes(list(catalog_idx.values()))
    groups=set((int(p.get('description_category_id') or 0),int(p.get('type_id') or 0)) for p in cards)
    names={}
    for g in groups:
        if 0 in g:continue
        try:
            names[g]={int(x.get('id') or 0):str(x.get('name') or '') for x in o.get_category_attributes(*g)}
        except Exception as e:print('META_ERROR',g,str(e)[:120],flush=True)
    report={'catalog':len(catalog),'attributes':len(cards),'missing_before':0,'code_filled_before':0,'candidates':0,'proposed':[],'unresolved':[],'write_errors':[],'tasks':[],'confirmed':[],'not_confirmed':[]}
    code_source=defaultdict(set)
    for p in cards:
        g=(int(p.get('description_category_id') or 0),int(p.get('type_id') or 0))
        v=o.attr_value_tuple(o.find_product_attr(p,22232) or {})
        if v and v[0]>0 and re.match(r'^\d{10}',v[1]):
            report['code_filled_before']+=1
            code_source[(g,v[1][:10])].add((int(v[0]),v[1]))
    unresolved=[]
    selection=[]
    for p in cards:
        previous=o.attr_value_tuple(o.find_product_attr(p,22232) or {})
        if previous:continue
        report['missing_before']+=1
        g=(int(p.get('description_category_id') or 0),int(p.get('type_id') or 0))
        code,reason=classify(p,names)
        if code:
            selection.append({'offer_id':p.get('offer_id'),'product_id':int(p.get('id') or p.get('product_id') or 0),'name':p.get('name'),'group':g,'code':code,'reason':reason})
        else:unresolved.append({'offer':p.get('offer_id'),'name':p.get('name'),'group':g,'reason':reason})
    report['candidates']=len(selection)
    dictionary_cache={}
    approved=[]
    for x in selection:
        k=(tuple(x['group']),x['code'])
        if k not in dictionary_cache:
            choices=code_source.get(k,set())
            if len(choices)==1:
                dictionary_cache[k]=next(iter(choices))
            else:
                try:
                    data=o.post('/v1/description-category/attribute/values/search',{'description_category_id':k[0][0],'type_id':k[0][1],'attribute_id':22232,'value':k[1],'limit':100})
                    matches=[v for v in data.get('result') or [] if str(v.get('value') or '').startswith(k[1]) and int(v.get('id') or 0)>0]
                    dictionary_cache[k]=(int(matches[0]['id']),str(matches[0]['value'])) if len(matches)==1 else None
                except Exception as e:
                    print('DICT_ERROR',k,str(e)[:100],flush=True);dictionary_cache[k]=None
        v=dictionary_cache[k]
        if v:approved.append(dict(x,dictionary_value_id=v[0],dictionary_value=v[1]))
        else:unresolved.append({'offer':x['offer_id'],'name':x['name'],'group':x['group'],'reason':'desired code not in Ozon category dictionary: '+x['code']})
    remaining,limit,usage,reset=o.get_daily_update_remaining()
    if remaining is not None and len(approved)>remaining:
        for x in approved[remaining:]:
            unresolved.append({'offer':x['offer_id'],'name':x['name'],'reason':'daily Ozon update limit'})
        approved=approved[:remaining]
    print('PLAN',json.dumps({'total':len(catalog),'missing':report['missing_before'],'candidate':len(selection),'approved':len(approved),'unresolved':len(unresolved)},ensure_ascii=False),flush=True)
    tasks=[]
    for batch in o.chunks(approved,40):
        payload={'items':[{'offer_id':x['offer_id'],'attributes':[{'complex_id':0,'id':22232,'values':[{'dictionary_value_id':x['dictionary_value_id'],'value':x['dictionary_value']}]}]} for x in batch]}
        try:
            d=o.post('/v1/product/attributes/update',payload)
            tasks.append(d.get('task_id'))
        except Exception as e:
            report['write_errors'].append({'offers':[x['offer_id'] for x in batch],'error':str(e)[:250]})
        time.sleep(.3)
    time.sleep(12)
    new=o.get_product_attributes([x['product_id'] for x in approved if x['product_id']])
    idx={str(x.get('offer_id')):x for x in new}
    for x in approved:
        p=idx.get(str(x['offer_id']))
        val=o.attr_value_tuple(o.find_product_attr(p,22232) or {}) if p else None
        if val and val[0]==x['dictionary_value_id']:
            report['confirmed'].append(x['offer_id'])
        else:report['not_confirmed'].append(x['offer_id'])
    report['tasks']=tasks
    report['proposed']=[{k:x[k] for k in ('offer_id','code','reason')} for x in approved]
    report['unresolved']=unresolved
    Path('ozon/tnved_finish_remaining_report_20261030.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('FINAL',json.dumps({k:v if not isinstance(v,list) else len(v) for k,v in report.items()},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
