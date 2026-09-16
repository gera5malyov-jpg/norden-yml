import parser
from parser import extract_variant_refs, load_one

BASE_URL = 'https://artikul-mebel.ru/catalog/detail/stul-uchenicheskiy-reguliruemyy-praym-plastik/'
VARIANT_URL = BASE_URL + '?oID=3295'

PAGE_HTML = '''
<html><body>
<h1>Стул ученический регулируемый «Прайм» пластик</h1>
<div class="product-detail">
  <div class="detail-article">Арт.: БП-00009258</div>
  <div class="detail-description">Стул ученический регулируемый «Прайм» предназначен для использования в учебных заведениях и личного пользования для детей и подростков.</div>
</div>
<div id="actual_price">
  <div>Розничная стоимость 4 537 ₽ / шт</div>
  <div>4 083.30 ₽ при заказе от 23 шт.</div>
</div>
<div class="cart-block" id="chars">
  <div class="cart-title"><div class="title">Технические характеристики</div></div>
  <div class="sku-chars"><div id="detail_sku_chars"></div></div>
</div>
<div class="cart-block" id="text2">
  <div class="cart-simple-text text-content">
    <p><b>Каркас:</b></p>
    <p>Труба плоскоовальная труба (Тип А - овал) 30 х 15 х 1,5</p>
    <p>Труба плоскоовальная труба (Тип А - овал) 45 х 25 х 1,5</p>
    <p>Труба круглая 20 х 1,2</p>
    <p>Заглушка овальная внутренняя размером 15х30</p>
    <p>Наконечник пластиковый для труб полуовального сечения 25х40</p>
    <p>Полоса 20 х 4 г_к 3 ПС 0,85</p>
    <p><b>Посадочное место:</b></p>
    <p>Сиденье PL Sigma</p>
    <p>Сиденье PL Sigma</p>
  </div>
</div>
<script>
var cfg = {'OFFERS':[{'ID':'3295','NAME':'Стул ученический регулируемый «Прайм» Пластик 5-7 гр. роста','NAME_HTML':'Стул ученический регулируемый «Прайм» Пластик 5-7 гр. роста','ARTICLE':'БП-00009258','ARTICLE_HTML':'БП-00009258','DETAIL_PAGE_URL':'/catalog/detail/stul-uchenicheskiy-reguliruemyy-praym-plastik/?oID=3295','CHECK_QUANTITY':false,'PRICE':{'PRICE':'4537'},'SKU_LIST_CHARS':[{'NAME':'Глубина','VALUE':'460 мм','HINT':''},{'NAME':'Ширина','VALUE':'460 мм','HINT':''},{'NAME':'Высота','VALUE':'835-915 мм','HINT':''},{'NAME':'Вес','VALUE':'6,64 кг','HINT':''}]}]};
</script>
</body></html>
'''


def test_variant_refs_include_selected_sku_characteristics():
    refs = extract_variant_refs(PAGE_HTML, BASE_URL)
    assert len(refs) == 1
    assert refs[0]['chars'] == {
        'Глубина': '460 мм',
        'Ширина': '460 мм',
        'Высота': '835-915 мм',
        'Вес': '6,64 кг',
    }


def test_load_one_adds_selected_characteristics_and_text2_to_description(monkeypatch):
    pages = {BASE_URL: PAGE_HTML, VARIANT_URL: PAGE_HTML}
    monkeypatch.setattr(parser, 'fetch', lambda url, **kwargs: pages[url])

    products = load_one(BASE_URL)
    assert len(products) == 1
    product = products[0]

    assert product['params']['Глубина'] == '460 мм'
    assert product['params']['Ширина'] == '460 мм'
    assert product['params']['Высота'] == '835-915 мм'
    assert product['params']['Вес'] == '6,64 кг'

    description = product['description']
    assert 'Технические характеристики:' in description
    assert 'Глубина: 460 мм' in description
    assert 'Ширина: 460 мм' in description
    assert 'Высота: 835-915 мм' in description
    assert 'Вес: 6,64 кг' in description
    assert 'Каркас:' in description
    assert 'Труба плоскоовальная труба (Тип А - овал) 30 х 15 х 1,5' in description
    assert 'Посадочное место:' in description
    assert 'Сиденье PL Sigma' in description

# Regression coverage for the live oID=3295 layout.
