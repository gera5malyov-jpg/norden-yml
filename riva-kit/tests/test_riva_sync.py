import os
import tempfile
import unittest

from riva_kit.feed import iter_offers, parse_categories
from riva_kit.mapper import characteristics_from_offer
from riva_kit.rules import calculate_prices, desired_stock, to_kit_sku
from riva_kit.sync import build_price_update, index_riva_variants, load_riva_variant_index, resolve_variant


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
        <param name="Код для сайта">SITE-1290597</param>
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
        <param name="Код для сайта">SITE-1290613</param>
        <param name="Цвет изделия">Венге</param>
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
        self.assertEqual(offers[0].kit_sku, 'SITE-1290597')
        self.assertEqual(offers[1].kit_sku, 'SITE-1290613')
        self.assertEqual(offers[0].article, 'Л.МП-1')
        self.assertFalse(offers[0].in_stock)
        self.assertTrue(offers[1].in_stock)
        self.assertEqual(offers[1].count, 7)

    def test_duplicate_article_uses_site_code_sku(self):
        offers = list(iter_offers(self.tmp.name))
        self.assertEqual(offers[0].article, offers[1].article)
        self.assertNotEqual(offers[0].kit_sku, offers[1].kit_sku)

    def test_mapper_keeps_useful_and_skips_internal(self):
        offer = list(iter_offers(self.tmp.name))[0]
        chars = dict(characteristics_from_offer(offer))
        self.assertIn('Артикул', chars)
        self.assertIn('Код для сайта', chars)
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

    def test_site_code_is_sku_without_prefix(self):
        self.assertEqual(to_kit_sku('SITE-1290597'), 'SITE-1290597')
        self.assertEqual(to_kit_sku('  000123  '), '000123')
        with self.assertRaises(ValueError):
            to_kit_sku('')

    def test_existing_old_sku_resolves_by_source_id_for_migration(self):
        titles = {'source-char': 'ID предложения Riva'}
        rows = [{
            'id': 'v1', 'sku': 'riva-1290613', 'name': 'Стол Л.МП-1 Венге', 'brand': 'RIVA',
            'characteristics': [{
                'characteristic_id': 'source-char', 'value': '1290613', 'values': ['1290613']
            }],
        }]
        by_source, by_sku, owned = index_riva_variants(rows, titles)
        self.assertEqual(owned, 1)
        offer = list(iter_offers(self.tmp.name))[1]
        variant = resolve_variant(offer, by_source, by_sku, titles)
        self.assertEqual(variant['id'], 'v1')
        self.assertEqual(offer.kit_sku, 'SITE-1290613')

    def test_duplicate_site_code_resolves_to_single_riva_variant(self):
        titles = {'source-char': 'ID предложения Riva'}
        row = {
            'id': 'v1', 'sku': 'DUP-1', 'name': 'Old name', 'brand': 'RIVA',
            'characteristics': [{
                'characteristic_id': 'source-char', 'value': '100', 'values': ['100']
            }],
        }
        by_source, by_sku, _ = index_riva_variants([row], titles)
        offer = list(iter_offers(self.tmp.name))[0]
        duplicate_offer = type(offer)(
            source_id='200', group_id=offer.group_id, article=offer.article,
            kit_sku='DUP-1', count=0, in_stock=False, category_id=offer.category_id,
            name='Different spelling', description=offer.description, price=offer.price,
            currency=offer.currency, barcode='', weight=offer.weight,
            source_url=offer.source_url, images=offer.images, params=offer.params,
        )
        self.assertEqual(resolve_variant(duplicate_offer, by_source, by_sku, titles)['id'], 'v1')

    def test_riva_index_scans_all_sku_prefixes(self):
        class FakeKit:
            def __init__(self):
                self.calls = []
            def iter_variants(self, filters=None):
                self.calls.append(filters)
                return iter([{
                    'id': 'v-uch',
                    'sku': 'УЧ-00000156',
                    'name': 'Кресло Атом',
                    'brand': 'RIVA',
                    'characteristics': [],
                }])

        kit = FakeKit()
        by_source, by_sku, owned = load_riva_variant_index(kit, {})
        self.assertEqual(kit.calls, [None])
        self.assertEqual(owned, 1)
        self.assertIn('УЧ-00000156', by_sku)
        self.assertEqual(by_sku['УЧ-00000156'][0]['id'], 'v-uch')

    def test_missing_site_code_is_skipped_without_stopping_feed(self):
        xml = SAMPLE.replace(
            '<param name="Код для сайта">SITE-1290597</param>',
            ''
        )
        tmp = tempfile.NamedTemporaryFile('w', suffix='.xml', delete=False, encoding='utf-8')
        try:
            tmp.write(xml)
            tmp.close()
            skipped = []
            offers = list(iter_offers(tmp.name, on_skip=skipped.append))
            self.assertEqual(len(offers), 1)
            self.assertEqual(offers[0].kit_sku, 'SITE-1290613')
            self.assertEqual(len(skipped), 1)
            self.assertIn('Код для сайта is missing', skipped[0])
        finally:
            os.unlink(tmp.name)


if __name__ == '__main__':
    unittest.main()
