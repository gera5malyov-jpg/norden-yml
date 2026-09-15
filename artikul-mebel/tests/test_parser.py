import xml.etree.ElementTree as ET

import parser
from parser import build_yml, extract_variant_refs, load_one, parse_product, parse_sitemap_urls

PRODUCT_HTML = '''
<html><head>
<meta property="og:image" content="/upload/catalog/luna-main.jpg">
<meta name="description" content="Стул LUNA F — мягкий стул на металлокаркасе.">
</head><body>
<div class="breadcrumbs"><a href="/catalog/">Каталог</a><a href="/catalog/stulya/">Стулья</a><a href="/catalog/myagkie-stulya/">Мягкие стулья</a></div>
<h1>Стул LUNA F</h1>
<div class="product-detail">
  <img src="/upload/catalog/luna-main.jpg"><img data-src="/upload/catalog/luna-side.webp">
  <div>Арт.: БП-00011041</div>
  <p>Новые мягкие стулья Luna, Luna F — классический дизайн и плавные линии.</p>
  <div>Розничная стоимость 8 360 ₽ / шт</div>
  <div>7 524 ₽ при заказе от 12 шт.</div>
  <div>В наличии:32</div>
  <h2>Технические характеристики</h2>
  <table>
    <tr><td>Габариты (ГхШхВ)</td><td>585х500х850 мм</td></tr>
    <tr><td>Вес</td><td>6 кг</td></tr>
  </table>
</div>
</body></html>
'''

BASE_VARIANT_URL = 'https://artikul-mebel.ru/catalog/detail/veshalka-garderobnaya-razbornaya-30-kryuchkov/'
VARIANT_1490_URL = BASE_VARIANT_URL + '?oID=2640'
VARIANT_1760_URL = BASE_VARIANT_URL + '?oID=2641'

VARIANT_BASE_HTML = '''
<html><body>
<h1>Вешалка гардеробная разборная 30 крючков</h1>
<div class="product-detail"><div>Цена по запросу</div></div>
<script>
var cfg = {'TREE_PROPS':[{'ID':'733','NAME':'Высота вешалки'}],
'OFFERS':[{'ID':'2640','NAME':'Вешалка гардеробная разборная 30 крючков Высота 1490 мм','NAME_HTML':'Вешалка гардеробная разборная 30 крючков Высота 1490 мм','ARTICLE':'БП-00006350','ARTICLE_HTML':'БП-00006350','DETAIL_PAGE_URL':'/catalog/detail/veshalka-garderobnaya-razbornaya-30-kryuchkov/?oID=2640','CHECK_QUANTITY':false,'PRICE':{'PRICE':'11110'}},
{'ID':'2641','NAME':'Вешалка гардеробная разборная 30 крючков Высота 1760 мм','NAME_HTML':'Вешалка гардеробная разборная 30 крючков Высота 1760 мм','ARTICLE':'БП-00006347','ARTICLE_HTML':'БП-00006347','DETAIL_PAGE_URL':'/catalog/detail/veshalka-garderobnaya-razbornaya-30-kryuchkov/?oID=2641','CHECK_QUANTITY':false,'PRICE':{'PRICE':'11227'}}]};
</script>
</body></html>
'''

VARIANT_1490_HTML = '''
<html><body><h1>Вешалка гардеробная разборная 30 крючков</h1>
<div class="product-detail">
<div>Арт.: БП-00006350</div>
<p>Вешалка гардеробная разборная, рассчитана на 30 крючков. Низкая модификация.</p>
<div>Розничная стоимость 11 110 ₽ / шт</div>
<div>9 999 ₽ при заказе от 10 шт.</div>
</div></body></html>
'''

VARIANT_1760_HTML = '''
<html><body><h1>Вешалка гардеробная разборная 30 крючков</h1>
<div class="product-detail">
<div>Арт.: БП-00006347</div>
<p>Вешалка гардеробная разборная, рассчитана на 30 крючков. Высокая модификация.</p>
<div>Розничная стоимость 11 227 ₽ / шт</div>
<div>10 104.30 ₽ при заказе от 9 шт.</div>
</div></body></html>
'''


def test_parse_product_extracts_core_fields():
    p = parse_product(PRODUCT_HTML, 'https://artikul-mebel.ru/catalog/detail/stul-luna-f/', 'Мягкие стулья')
    assert p['name'] == 'Стул LUNA F'
    assert p['sku'] == 'БП-00011041'
    assert p['price'] == 8360.0
    assert p['bulk_price'] == 7524.0
    assert p['bulk_min_qty'] == 12
    assert p['stock'] == 32
    assert p['category'] == 'Мягкие стулья'
    assert p['params']['Габариты (ГхШхВ)'] == '585х500х850 мм'
    assert p['params']['Вес'] == '6 кг'
    assert p['pictures'] == [
        'https://artikul-mebel.ru/upload/catalog/luna-main.jpg',
        'https://artikul-mebel.ru/upload/catalog/luna-side.webp',
    ]


def test_build_yml_contains_offer_and_characteristics():
    product = parse_product(PRODUCT_HTML, 'https://artikul-mebel.ru/catalog/detail/stul-luna-f/', 'Мягкие стулья')
    data = build_yml([product])
    root = ET.fromstring(data)
    shop = root.find('shop')
    assert shop.findtext('name') == 'Артикул-Мебель'
    offer = shop.find('./offers/offer')
    assert offer.findtext('price') == '8360'
    assert offer.findtext('vendorCode') == 'БП-00011041'
    assert offer.findtext('picture').endswith('luna-main.jpg')
    params = {p.attrib['name']: p.text for p in offer.findall('param')}
    assert params['Габариты (ГхШхВ)'] == '585х500х850 мм'
    assert params['Оптовая цена'] == '7524'
    assert params['Минимальное количество для оптовой цены'] == '12'
    assert params['Остаток'] == '32'


def test_parse_sitemap_urls_supports_urlset():
    xml = b'''<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://artikul-mebel.ru/catalog/detail/a/</loc></url>
      <url><loc>https://artikul-mebel.ru/blog/x/</loc></url>
    </urlset>'''
    urls, nested = parse_sitemap_urls(xml)
    assert urls == ['https://artikul-mebel.ru/catalog/detail/a/']
    assert nested == []


def test_extract_variant_refs_from_bitrix_offers():
    refs = extract_variant_refs(VARIANT_BASE_HTML, BASE_VARIANT_URL)
    assert refs == [
        {
            'id': '2640',
            'name': 'Вешалка гардеробная разборная 30 крючков Высота 1490 мм',
            'sku': 'БП-00006350',
            'url': VARIANT_1490_URL,
            'variant': 'Высота 1490 мм',
        },
        {
            'id': '2641',
            'name': 'Вешалка гардеробная разборная 30 крючков Высота 1760 мм',
            'sku': 'БП-00006347',
            'url': VARIANT_1760_URL,
            'variant': 'Высота 1760 мм',
        },
    ]


def test_load_one_expands_variants_and_uses_selected_prices(monkeypatch):
    pages = {
        BASE_VARIANT_URL: VARIANT_BASE_HTML,
        VARIANT_1490_URL: VARIANT_1490_HTML,
        VARIANT_1760_URL: VARIANT_1760_HTML,
    }
    monkeypatch.setattr(parser, 'fetch', lambda url, **kwargs: pages[url])

    products = load_one(BASE_VARIANT_URL)
    assert len(products) == 2
    by_sku = {p['sku']: p for p in products}

    low = by_sku['БП-00006350']
    assert low['name'].endswith('Высота 1490 мм')
    assert low['url'] == VARIANT_1490_URL
    assert low['price'] == 11110.0
    assert low['bulk_price'] == 9999.0
    assert low['bulk_min_qty'] == 10
    assert low['params']['Модификация'] == 'Высота 1490 мм'

    high = by_sku['БП-00006347']
    assert high['name'].endswith('Высота 1760 мм')
    assert high['url'] == VARIANT_1760_URL
    assert high['price'] == 11227.0
    assert high['bulk_price'] == 10104.30
    assert high['bulk_min_qty'] == 9
    assert high['params']['Модификация'] == 'Высота 1760 мм'
