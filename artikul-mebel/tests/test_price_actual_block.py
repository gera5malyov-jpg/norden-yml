from parser import parse_product


URL = 'https://artikul-mebel.ru/catalog/detail/stol-mobilnyy-s-kolesami/'


def test_parse_product_reads_unlabelled_retail_price_from_actual_price():
    html = '''
    <html><body>
      <h1>Стол мобильный с колесами</h1>
      <div class="product-detail">
        <div class="detail-article">Арт.: TEST-PRICE</div>
      </div>
      <div id="actual_price">
        <div>10 515 ₽ / шт</div>
        <div>9 463.50 ₽ при заказе от 10 шт.</div>
      </div>
    </body></html>
    '''

    product = parse_product(html, URL)

    assert product['price'] == 10515.0
    assert product['bulk_price'] == 9463.50
    assert product['bulk_min_qty'] == 10
