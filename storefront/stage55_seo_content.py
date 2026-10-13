#!/usr/bin/env python3
"""Pure, bounded SEO proposals from a read-only production snapshot.

No network, credentials or database writes. Apply only with a fresh comparison
of every expected field, feature digest, active category and SKU availability.
Only metadata and previously blank descriptions may be changed.
"""
from __future__ import annotations
import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import html
import json
from pathlib import Path
import re

# Reviewed existing routes; no city pages, category reassignments or new URLs.
CATEGORY_COPY = {
 'stul/stulya': ('Обеденные стулья', 'Сравните обеденные стулья по размерам, материалам и цвету. Характеристики и цены моделей в каталоге Мегаполис; оформление заказа онлайн.'),
 'stoly_1/stoly-zhurnalnye': ('Журнальные столы', 'Журнальные столы в каталоге Мегаполис. Сравните размеры, материалы и форму моделей, проверьте характеристики и цену перед заказом.'),
 'stoly_1/stoly': ('Обеденные столы', 'Обеденные столы в каталоге Мегаполис. Сравните размеры и материалы моделей, проверьте характеристики и комплектацию перед оформлением заказа.'),
 'kompyuternye-kresla/stulya-posetiteley': ('Стулья для посетителей', 'Стулья для посетителей в каталоге Мегаполис. Сравните размеры, материал каркаса и сиденья по карточкам моделей; проверьте цену перед заказом.'),
 'shkafy/stellazhi': ('Стеллажи', 'Стеллажи в каталоге Мегаполис. Сравните ширину, высоту, глубину и материалы по карточкам моделей, проверьте комплектацию и цену перед заказом.'),
 'shkafy/shkafy-raspashnye': ('Распашные шкафы', 'Распашные шкафы в каталоге Мегаполис. Сравните размеры, материалы корпуса и фасадов, проверьте комплектацию модели и цену перед заказом.'),
 'stoly_1/stoly-pismennye': ('Письменные столы', 'Письменные столы в каталоге Мегаполис. Сравните размеры рабочей поверхности, материалы и комплектацию по карточкам моделей перед заказом.'),
}
# Human-reviewed factual fields. Internal/import/supplier IDs, promises,
# warranty and load limits are intentionally excluded.
SPEC_LABELS = {
 'vendor':'Производитель', 'material':'Материал', 'material_karkasa':'Материал каркаса',
 'material_sidenya':'Материал сиденья', 'material_stoleshnitsy':'Материал столешницы',
 'color':'Цвет', 'kollektsiya':'Коллекция', 'forma':'Форма',
 'material_korpusa':'Материал корпуса', 'material_obivki':'Материал обивки',
 'obivka':'Обивка', 'seriya':'Серия', 'strana':'Страна', 'stil':'Стиль',
 'forma_stoleshnitsy':'Форма столешницы', 'tsvet_stoleshnitsy':'Цвет столешницы',
 'podlokotniki':'Подлокотники', 'mekhanizm_kachaniya':'Механизм качания',
 'kolichestvo_yashchikov':'Количество ящиков', 'kolichestvo_dverey':'Количество дверей',
 'shirina':'Ширина', 'vysota':'Высота', 'glubina':'Глубина', 'weight':'Вес',
}
DIMENSIONS = {'shirina','vysota','glubina','weight'}


def number(value):
    try:
        result=Decimal(str(value))
        return result if result.is_finite() else Decimal(0)
    except (InvalidOperation, ValueError):
        return Decimal(0)


def fingerprint(features):
    """Canonical digest; deployment must recompute from freshly read features."""
    encoded=json.dumps(features,ensure_ascii=False,sort_keys=True,separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def text_value(value):
    if isinstance(value,dict):
        if {'feature_id','sort','cml1c_id'} & set(value):return ''
        parts=[text_value(v) for v in value.values()]
        return ', '.join(dict.fromkeys(v for v in parts if v))
    if isinstance(value,list):
        return ', '.join(dict.fromkeys(v for v in map(text_value,value) if v))
    if value is None or isinstance(value,bool):
        return ''
    result=' '.join(str(value).split())
    return result if 0<len(result)<=220 and result.lower() not in {'-','нет данных','не указано','null','n/a'} else ''


def build_category_proposals(category_rows, paid_rows, limit=6):
    paid={str(r['category_id']):r for r in paid_rows if r.get('category_id')}
    rows=sorted(category_rows,key=lambda r:(
        number(paid.get(str(r.get('id')),{}).get('item_revenue',0)),
        number(r.get('products',0))),reverse=True)
    result=[]
    for row in rows:
        route=row.get('full_url','').strip('/')
        if route not in CATEGORY_COPY or number(row.get('products'))<=0:
            continue
        title,description=CATEGORY_COPY[route]
        changes={}
        for key,value in [('meta_title',title+' - купить | Мегаполис'),('meta_description',description)]:
            if not str(row.get(key) or '').strip():changes[key]=value
        if not changes:continue
        signal=paid.get(str(row['id']),{})
        result.append(dict(id=int(row['id']),url='/category/'+route+'/',set=changes,
            expected={k:row.get(k) for k in ('name','full_url','meta_title','meta_description')},
            evidence=dict(active_catalog_products=int(row['products']),
                paid_orders_90d=int(signal.get('paid_orders',0)),
                item_revenue_90d=str(signal.get('item_revenue','0')))))
        if len(result)>=min(max(limit,0),6):break
    return result if limit>0 else []


def build_product_proposals(product_rows, feature_definitions, *, category_rows, limit=50):
    categories={str(r['id']):r['full_url'].strip('/') for r in category_rows}
    definitions={r['code']:r for r in feature_definitions if r.get('code')}
    result=[];seen=set()
    for p in product_rows:
        ident=str(p.get('id',''));cat=str(p.get('category_id',''))
        if not ident.isdigit() or ident in seen or cat not in categories:continue
        if p.get('category_url','').strip('/')!=categories[cat]:continue
        if p.get('status','1') not in (1,'1') or number(p.get('price'))<=0:continue
        if str(p.get('description') or '').strip():continue
        if p.get('count') is not None and number(p.get('count'))<=0:continue
        skus=p.get('skus') or []
        if not any(str(s.get('available'))=='1' and number(s.get('price'))>0 and (s.get('count') is None or number(s.get('count'))>0) for s in skus):continue
        name=text_value(p.get('name'));slug=p.get('url','')
        if not name or not slug or any(c in slug for c in '/?#\\'):continue
        features=p.get('features')
        if not isinstance(features,dict):continue
        specs=[]
        for code,label in SPEC_LABELS.items():
            value=text_value(features.get(code))
            if not value:continue
            if code in DIMENSIONS:
                # Units must be explicit in rendered values or confirmed by
                # feature metadata. Never infer cm/mm from the value's size.
                definition=definitions.get(code,{})
                unit=text_value(definition.get('unit'))
                if not unit and str(definition.get('type','')).startswith('dimension.'):
                    unit={'mm':'мм','cm':'см','m':'м','kg':'кг','g':'г'}.get(definition.get('default_unit'),'')
                if not unit:
                    match=re.search(r'(?:[,\s(]|^(?:Высота|Ширина|Глубина|Длина|Вес))(мм|см|кг|г|м)\)?$',definition.get('name',''),re.I)
                    unit=match.group(1).lower() if match else ''
                if unit and re.fullmatch(r'[\d\s.,]+',value):value+=' '+unit
                if not re.search(r'\b(?:мм|см|м|кг|г)\b',value):continue
            specs.append((label,value))
        if len(specs)<4:continue
        specs=specs[:6]
        description='<p>'+html.escape(name)+'</p>\n<ul>\n'+''.join(
            '<li>'+html.escape(label)+': '+html.escape(value)+'</li>\n' for label,value in specs)+'</ul>'
        expected={k:p.get(k) for k in ('name','url','category_id','category_url','price','count','description')}
        expected['features_sha256']=fingerprint(features)
        expected['sku_availability']=[{k:s.get(k) for k in ('id','price','count','available')} for s in skus]
        expected['status']=p.get('status','1')
        result.append(dict(id=int(ident),url='/'+slug+'/',set={'description':description},expected=expected))
        seen.add(ident)
    # Interleave verified categories; use recorded positive stock before any
    # unknown-stock orderable candidates. Never fill a category quota with junk.
    selected=[]
    for known_stock in (True,False):
        groups={cat:[] for cat in categories}
        for row in result:
            if (row['expected']['count'] is not None)==known_stock:
                groups[str(row['expected']['category_id'])].append(row)
        while any(groups.values()) and len(selected)<min(max(limit,0),50):
            for group in groups.values():
                if group and len(selected)<min(max(limit,0),50):selected.append(group.pop(0))
    return selected


def build_proposal(facts):
    categories=facts.get('categories',[])
    return dict(schema_version=1,site='https://profikompany.ru',
        categories=build_category_proposals(categories,facts.get('paid_categories_90d',[])),
        products=build_product_proposals(facts.get('products',[]),facts.get('feature_definitions',[]),category_rows=categories),
        limitations=['Paid category aggregates are a 90-day historical storefront subset, not profit or search volume.',
        'Wordstat and Webmaster query statistics unavailable; no traffic or ranking claim.',
        'Unknown stock quantities are allowed only for explicitly orderable SKUs; no stock promise. Missing factual specifications are skipped.',
        'Apply only after fresh CAS checks; no product URLs, categories, prices, stock or integrations may change.'])


SITEMAP_INIT_ANCHOR = "        $real_domain = $this->routing->getDomain(null, true, false);"
SITEMAP_PRODUCT_ANCHOR = """                    $this->addUrl(
                        $url,
                        $p['edit_datetime'] ? $p['edit_datetime'] : $p['create_datetime'],
                        self::CHANGE_MONTHLY,
                        0.8
                    );
                    $count_adding++;"""
SITEMAP_PAGE_ANCHOR = """                        $this->addUrl(
                            $url.$pp['url'].'/',
                            $pp['update_datetime'] ? $pp['update_datetime'] : $pp['create_datetime'],
                            self::CHANGE_MONTHLY,
                            0.4
                        );
                        $count_adding++;"""


def build_sitemap_duplicate_patch(source):
    """Optional minimal patch: suppress repeated product URLs within a child XML.

    Pure source transform; never modifies product slugs/IDs or SQL pagination.
    Host-scoped emission only; other storefronts retain original behavior.
    Cross-child duplicates and product-page counting discrepancy remain separate
    investigations. Apply only against the audited source with backup and XML QA.
    """
    marker='profikompany_seen_product_urls'
    anchors=(SITEMAP_INIT_ANCHOR,SITEMAP_PRODUCT_ANCHOR,SITEMAP_PAGE_ANCHOR)
    if marker in source or any(source.count(a)!=1 for a in anchors):
        raise ValueError('Sitemap source differs from audited version or already patched')
    result=source.replace(SITEMAP_INIT_ANCHOR,SITEMAP_INIT_ANCHOR+
        '\n        // Scoped duplicate emission guard; preserve catalog and pagination.\n'
        '        $profikompany_seen_product_urls = array();',1)
    for anchor,expression,indent in ((SITEMAP_PRODUCT_ANCHOR,'$url','                    '),
                                   (SITEMAP_PAGE_ANCHOR,"$url.$pp['url'].'/'",'                        ')):
        call,counter=anchor.rsplit('\n',1)
        guarded=(indent+'$profikompany_output_url = '+expression+';\n'+
            indent+"$profikompany_scoped_url = strtolower((string) parse_url($profikompany_output_url, PHP_URL_HOST)) === 'profikompany.ru';\n"+
            indent+'if (!$profikompany_scoped_url || !isset($profikompany_seen_product_urls[$profikompany_output_url])) {\n'+
            '\n'.join('    '+line for line in call.splitlines())+'\n'+
            indent+'    if ($profikompany_scoped_url) {\n'+
            indent+'        $profikompany_seen_product_urls[$profikompany_output_url] = true;\n'+
            indent+'    }\n'+indent+'}\n'+counter)
        result=result.replace(anchor,guarded,1)
    return result


CATEGORY_HEAD_SHA256 = '8aac2e31d06d5afbe5688b1e67b4b0f5b5a7dec5f6b3fffac1cc3012ec3e7a28'
CATEGORY_HEAD_ANCHOR = """    {if !empty($_info_title)}
      <title>{$_info_title|escape}</title>
    {else}
      <title>{$wa->title()|escape}</title>
    {/if}
"""
CATEGORY_HEAD_ROUTES = {
    285:'stul/stulya',438:'stoly_1/stoly-zhurnalnye',271:'stoly_1/stoly',
    4201:'kompyuternye-kresla/stulya-posetiteley',279:'shkafy/stellazhi',
    2150:'shkafy/shkafy-raspashnye',
}


def patch_category_metadata_head(source, categories, expected_sha256=None):
    """Use saved native metadata for six clean canonical category URLs only.

    Production shopFrontendCategoryAction sets native response metadata before
    frontend_category. SEO frontendHead then applyOuter replaces it with its
    priority-2 forced templates. The verified shopViewHelper.category reads the
    native category row and SEO has no view_category handler. Rendering these two
    saved fields directly leaves plugin hooks, keywords, OG, H1 and body intact.
    Every query variant keeps the original plugin-controlled head.
    """
    if fingerprint_text(source)!=(expected_sha256 or CATEGORY_HEAD_SHA256):
        raise ValueError('Audited category head source changed')
    marker='megapolis-native-category-meta-stage55'
    if source.count(CATEGORY_HEAD_ANCHOR)!=1 or marker in source:
        raise ValueError('Category title anchor changed or already patched')
    if not 1<=len(categories)<=6:raise ValueError('Unexpected category metadata scope')
    ids=set();branches=[]
    for row in categories:
        ident=row['id'];route=CATEGORY_HEAD_ROUTES.get(ident)
        if ident in ids or not route or row['url']!='/category/'+route+'/':
            raise ValueError('Unverified category ID/route')
        ids.add(ident)
        tag='if' if not branches else 'elseif'
        branches.append("      {"+tag+" $_megapolis_meta_url == '/category/"+route+"/'}\n"+
                        "        {$_megapolis_meta_category = $wa->shop->category("+str(ident)+")}\n"+
                        "        {$_megapolis_meta_expected_url = '"+route+"'}")
    block="""    {* megapolis-native-category-meta-stage55: saved metadata on clean canonical pages only *}
    {if $wa->domainUrl() == 'https://profikompany.ru' && $wa->param('action') == 'category' && $wa->currentUrl(false) == $wa->currentUrl(false, true)}
      {$_megapolis_meta_url = $wa->currentUrl(false, true)}
      {$_megapolis_meta_category = []}
      {$_megapolis_meta_expected_url = ''}
"""+'\n'.join(branches)+"""
      {/if}
      {if !empty($_megapolis_meta_category) && $_megapolis_meta_category.status == 1 && $_megapolis_meta_category.full_url == $_megapolis_meta_expected_url}
        {if !empty($_megapolis_meta_category.meta_title)}{$_info_title = $_megapolis_meta_category.meta_title}{/if}
        {if !empty($_megapolis_meta_category.meta_description)}{$_info_description = $_megapolis_meta_category.meta_description}{/if}
      {/if}
    {/if}
"""
    return source.replace(CATEGORY_HEAD_ANCHOR,block+CATEGORY_HEAD_ANCHOR,1)


def fingerprint_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot',type=Path)
    args=parser.parse_args()
    print(json.dumps(build_proposal(json.loads(args.snapshot.read_text())),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
