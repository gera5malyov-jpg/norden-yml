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

PRICE_OUTSIDE_DETAIL_HTML = '''
<html><body><h1>Вешалка гардеробная разборная 30 крючков</h1>
<div class="product-detail">
  <div>Арт.: БП-00006350</div>
  <p>Вешалка гардеробная разборная, рассчитана на 30 крючков. Низкая модификация.</p>
</div>
<div id="actual_price">
  <div>Розничная стоимость</div>
  <div>11 110 ₽ / шт</div>
  <div>9 999 ₽ при заказе от 10 шт.</div>
</div>
</body></html>
'''

RICH_PRODUCT_HTML = '''
<html><head>
<meta property="og:image" content="/upload/catalog/mini-og.jpg">
</head><body>
<div class="breadcrumbs"><a href="/catalog/">Каталог</a><a href="/catalog/stulya/">Стулья</a><a href="/catalog/ofisnye-stulya/">Офисные стулья</a></div>
<h1>Стул MINI OFFICE 2 (МИНИ ОФИС)</h1>
<div class="wrapper-big-picture">
  <img data-src="/upload/resize_cache/mini-1.webp" data-big-src="/upload/catalog/mini-1.jpg">
  <img data-src="/upload/resize_cache/mini-2.webp" data-big-src="/upload/catalog/mini-2.jpg">
</div>
<div class="product-detail">
  <div>Арт.: БП-00016674</div>
  <div class="detail-description">Стул MINI OFFICE - лаконичный дизайн и прекрасная эргономика для вашего офиса. Вы можете выбрать ножки самостоятельно.</div>
</div>
<div id="actual_price"><div>Розничная стоимость 6 675 ₽ / шт</div><div>6 007.50 ₽ при заказе от 15 шт.</div></div>
<section id="chars">
  <h2>Технические характеристики</h2>
  <div class="char-row"><span class="char-name">Габариты (ГхШхВ)</span><span class="char-value">560х460x810 мм</span></div>
  <div class="char-row"><span class="char-name">Вес</span><span class="char-value">5 кг</span></div>
  <div class="char-group"><h3>Каркас</h3><ul><li>Труба круглая 25 х 1,2</li><li>Порошковая покраска.</li></ul></div>
  <div class="char-group"><h3>Сиденье</h3><ul><li>Фанера 6мм, Поролон 5мм</li><li>Обивка кожзаменитель или ткань.</li></ul></div>
</section>
<section id="description"><h2>Описание</h2><p>Стул специально разработан для комфортной работы сотрудников офиса. Специальная прострочка на спинке подчеркивает минималистичный дизайн.</p></section>
</body></html>
'''

ARTICLE_OUTSIDE_DETAIL_HTML = '''
<html><body>
<header>Артикул-Мебель</header>
<h1>Стул MINI OFFICE 2 (МИНИ ОФИС)</h1>
<div class="product-detail"><p>Карточка товара без артикула внутри этого контейнера.</p></div>
<div class="detail-article">Арт.: БП-00016674</div>
<div id="actual_price">Розничная стоимость 6 675 ₽ / шт</div>
</body></html>
'''

REAL_CHARS_WRAPPERS_HTML = '''
<html><body>
<h1>Стул тестовый</h1>
<div class="product-detail"><div>Арт.: TEST-1</div></div>
<div id="actual_price">Розничная стоимость 1 000 ₽ / шт</div>
<div id="chars">
  <div class="cart-title"><div class="title">Технические характеристики</div><div class="line"></div></div>
  <div class="cart-char">
    <div class="row">
      <div class="cart-char-table-wrap">
        <table><tr><td class="left">Габариты (ГхШхВ)</td><td class="dotted"></td><td class="right bold">560х460x810 мм</td></tr></table>
        <table><tr><td class="left">Вес</td><td class="dotted"></td><td class="right bold">5 кг</td></tr></table>
      </div>
      <div class="drawing"><div>Чертеж</div><div>Все габаритные размеры</div></div>
    </div>
  </div>
</div>
</body></html>
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


def test_parse_product_reads_price_outside_detail_container():
    product = parse_product(PRICE_OUTSIDE_DETAIL_HTML, VARIANT_1490_URL)
    assert product['price'] == 11110.0
    assert product['bulk_price'] == 9999.0
    assert product['bulk_min_qty'] == 10


def test_parse_product_combines_descriptions_characteristics_and_gallery():
    product = parse_product(RICH_PRODUCT_HTML, 'https://artikul-mebel.ru/catalog/detail/stul-mini-office-2/')

    assert product['description'] == (
        'Стул MINI OFFICE - лаконичный дизайн и прекрасная эргономика для вашего офиса. '
        'Вы можете выбрать ножки самостоятельно. '
        'Стул специально разработан для комфортной работы сотрудников офиса. '
        'Специальная прострочка на спинке подчеркивает минималистичный дизайн.'
    )
    assert product['params']['Габариты (ГхШхВ)'] == '560х460x810 мм'
    assert product['params']['Вес'] == '5 кг'
    assert product['params']['Каркас'] == 'Труба круглая 25 х 1,2; Порошковая покраска.'
    assert product['params']['Сиденье'] == 'Фанера 6мм, Поролон 5мм; Обивка кожзаменитель или ткань.'
    assert product['pictures'][:3] == [
        'https://artikul-mebel.ru/upload/catalog/mini-og.jpg',
        'https://artikul-mebel.ru/upload/catalog/mini-1.jpg',
        'https://artikul-mebel.ru/upload/catalog/mini-2.jpg',
    ]


def test_parse_product_uses_real_article_marker_not_brand_name():
    product = parse_product(ARTICLE_OUTSIDE_DETAIL_HTML, 'https://artikul-mebel.ru/catalog/detail/stul-mini-office-2/')
    assert product['sku'] == 'БП-00016674'


def test_characteristics_do_not_include_wrapper_or_drawing_junk():
    product = parse_product(REAL_CHARS_WRAPPERS_HTML, 'https://artikul-mebel.ru/catalog/detail/test/')
    assert product['params'] == {
        'Габариты (ГхШхВ)': '560х460x810 мм',
        'Вес': '5 кг',
    }
