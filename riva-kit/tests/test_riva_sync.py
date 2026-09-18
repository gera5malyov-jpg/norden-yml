import os
import tempfile
import unittest
from decimal import Decimal

from riva_kit.feed import iter_offers, parse_categories
from riva_kit.mapper import characteristics_from_offer
from riva_kit.rules import desired_stock, to_kit_sku
from riva_kit.sync import index_riva_variants


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
        <name>Стол Л.МП-1</name>
        <barcode>2000000007748</barcode>
        <param name="Артикул">Л.МП-1</param>
        <param name="Цвет изделия">Белый</param>
        <param name="Количество на складе «Склад СПБ»">0</param>
        <param name="РРЦ: Цена">11657</param>
        <weight>32.8</weight>
        <count>0</count>
      </offer>
      <offer id="1290601" available="true" group_id="1290291">
        <price>13407</price>
        <currencyId>RUR</currencyId>
        <categoryId>2</categoryId>
        <name>Стол Л.МП-1 другой цвет</name>
        <param name="Артикул">Л.МП-1</param>
        <param name="Цвет изделия">Акация</param>
        <count>7</count>
      </offer>
    </offers>
  </shop>
</yml_catalog>
'''


class RivaFeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile('w', suffix='.xml', delete=False, encoding='utf-8')
        self.tmp.write(SAMPLE)
        self.tmp.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_categories_and_offers(self):
        categories = parse_categories(self.tmp.name)
        self.assertEqual(categories['2'].parent_id, '1')
        offers = list(iter_offers(self.tmp.name))
        self.assertEqual(len(offers), 2)
        self.assertEqual(offers[0].kit_sku, 'riva-1290597')
        self.assertEqual(offers[0].article, 'Л.МП-1')
        self.assertFalse(offers[0].in_stock)
        self.assertTrue(offers[1].in_stock)
        self.assertEqual(offers[1].count, 7)

    def test_duplicate_article_does_not_collapse_sku(self):
        offers = list(iter_offers(self.tmp.name))
        self.assertEqual(offers[0].article, offers[1].article)
        self.assertNotEqual(offers[0].kit_sku, offers[1].kit_sku)

    def test_mapper_keeps_useful_and_skips_internal(self):
        offer = list(iter_offers(self.tmp.name))[0]
        chars = dict(characteristics_from_offer(offer))
        self.assertIn('Артикул', chars)
        self.assertIn('Цвет изделия', chars)
        self.assertIn('Штрихкод', chars)
        self.assertNotIn('Количество на складе «Склад СПБ»', chars)
        self.assertNotIn('РРЦ: Цена', chars)

    def test_stock_modes(self):
        self.assertEqual(desired_stock(0, 'binary100'), 0)
        self.assertEqual(desired_stock(7, 'binary100'), 100)
        self.assertEqual(desired_stock(7, 'actual'), 7)

    def test_prefix_index_is_isolated(self):
        index, dup = index_riva_variants([
            {'id': 'r1', 'sku': 'riva-1290597'},
            {'id': 'l1', 'sku': 'liga-1290597'},
        ])
        self.assertEqual(set(index), {'riva-1290597'})
        self.assertEqual(dup, {})

    def test_sku_requires_offer_id(self):
        self.assertEqual(to_kit_sku('1290597'), 'riva-1290597')
        with self.assertRaises(ValueError):
            to_kit_sku('')


if __name__ == '__main__':
    unittest.main()
