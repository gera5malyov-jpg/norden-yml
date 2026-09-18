import os
import tempfile
import unittest

from riva_kit.feed import iter_offers, parse_categories
from riva_kit.mapper import characteristics_from_offer
from riva_kit.rules import calculate_prices, desired_stock, to_kit_sku
from riva_kit.sync import build_price_update, index_riva_variants, resolve_variant


SAMPLE = '''<?xml version="1.0" encoding="UTF-8"?>
<yml_catalog>
  <shop>
    <categories>
      <category id="1">Мебель</category>
      <category id="2" parentId="1">Столы</category>
    </categories>
    <offers>
      <offer id="1290597" available="true" group_id="1290287">
        <url>https://riva.ru/item/1290287/</url>
        <price>8039</price>
        <currencyId>RUR</currencyId>
        <categoryId>2</categoryId>
        <picture>https://riva.ru/a.png</picture>
        <name>Стол Л.МП-1 Белый</name>
        <barcode>2000000007748</barcode>
        <param name="Артикул">Л.МП-1</param>
        <param name="Цвет изделия">Белый</param>
        <param name="Количество на складе «Склад СПБ»">0</param>
        <param name="РРЦ: Цена">11657</param>
        <weight>32.8</weight>
        <count>0</count>
      </offer>
      <offer id="1290613" available="true" group_id="1290287">
        <price>8039</price>
        <currencyId>RUR</currencyId>
        <categoryId>2</categoryId>
        <name>Стол Л.МП-1 Венге</name>
        <barcode>2000000009674</barcode>
        <param name="Артикул">Л.МП-1</param>
        <param name="Цвет изделия">Венге</param>
        <count>7</count>
      </offer>
    </offers>
  </shop>
</yml_catalog>
'''


class RivaFeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            'w', suffix='.xml', delete=False, encoding='utf-8'
        )
        self.tmp.write(SAMPLE)
        self.tmp.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_categories_and_offers(self):
        categories = parse_categories(self.tmp.name)
        self.assertEqual(categories['2'].parent_id, '1')
        offers = list(iter_offers(self.tmp.name))
        self.assertEqual(len(offers), 2)
        self.assertEqual(offers[0].kit_sku, 'Л.МП-1')
        self.assertEqual(offers[1].kit_sku, 'Л.МП-1')
        self.assertEqual(offers[0].article, 'Л.МП-1')
        self.assertFalse(offers[0].in_stock)
        self.assertTrue(offers[1].in_stock)
        self.assertEqual(offers[1].count, 7)

    def test_duplicate_article_keeps_exact_same_sku(self):
        offers = list(iter_offers(self.tmp.name))
        self.assertEqual(offers[0].article, offers[1].article)
        self.assertEqual(offers[0].kit_sku, offers[1].kit_sku)

    def test_mapper_keeps_useful_and_skips_internal(self):
        offer = list(iter_offers(self.tmp.name))[0]
        chars = dict(characteristics_from_offer(offer))
        self.assertIn('Артикул', chars)
        self.assertIn('Цвет изделия', chars)
        self.assertIn('Штрихкод', chars)
        self.assertIn('ID предложения Riva', chars)
        self.assertNotIn('Количество на складе «Склад СПБ»', chars)
        self.assertNotIn('РРЦ: Цена', chars)

    def test_riva_stock_rule(self):
        self.assertEqual(desired_stock(0), 100)
        self.assertEqual(desired_stock(7), 7)

    def test_riva_price_rule(self):
        prices = calculate_prices('1000')
        self.assertEqual(str(prices['old']), '1800.00')
        self.assertEqual(str(prices['sale']), '1260.00')
        self.assertEqual(str(prices['minimum']), '1200.00')

    def test_api_price_payload_uses_supported_fields_only(self):
        offer = list(iter_offers(self.tmp.name))[0]
        variant = {'id': 'v1', 'pricing': {}}
        update = build_price_update(offer, variant)
        self.assertEqual(update['price'], '14470.20')
        self.assertEqual(update['manual_discount_price'], '10129.14')
        self.assertNotIn('minimum_price', update)

    def test_exact_article_is_sku(self):
        self.assertEqual(to_kit_sku('Л.МП-1'), 'Л.МП-1')
        with self.assertRaises(ValueError):
            to_kit_sku('')

    def test_duplicate_sku_variants_resolve_by_source_id(self):
        titles = {'source-char': 'ID предложения Riva'}
        rows = [
            {
                'id': 'v1',
                'sku': 'Л.МП-1',
                'name': 'Стол Л.МП-1 Белый',
                'brand': 'RIVA',
                'characteristics': [
                    {
                        'characteristic_id': 'source-char',
                        'value': '1290597',
                        'values': ['1290597'],
                    }
                ],
            },
            {
                'id': 'v2',
                'sku': 'Л.МП-1',
                'name': 'Стол Л.МП-1 Венге',
                'brand': 'RIVA',
                'characteristics': [
                    {
                        'characteristic_id': 'source-char',
                        'value': '1290613',
                        'values': ['1290613'],
                    }
                ],
            },
        ]
        by_source, by_sku, owned = index_riva_variants(rows, titles)
        self.assertEqual(owned, 2)
        self.assertEqual(len(by_sku['Л.МП-1']), 2)
        offer = list(iter_offers(self.tmp.name))[1]
        variant = resolve_variant(offer, by_source, by_sku, titles)
        self.assertEqual(variant['id'], 'v2')


if __name__ == '__main__':
    unittest.main()
