from parser import parse_product, build_yml, parse_sitemap_urls
import xml.etree.ElementTree as ET

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
