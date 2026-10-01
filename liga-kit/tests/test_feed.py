import os
import tempfile
import unittest

from liga_kit.feed import parse_feed


XML = """<?xml version="1.0" encoding="utf-8"?>
<yml_catalog>
  <shop>
    <categories>
      <category id="5317">Диваны</category>
      <category id="6632" parentId="5317">Прямые диваны</category>
    </categories>
    <offers>
      <offer id="115615" available="true" group_id="6632">
        <url>https://ligadivanov.ru/item/109775</url>
        <price>87990</price>
        <currencyId>RUB</currencyId>
        <categoryId>6632</categoryId>
        <picture>https://img/1.jpg,https://img/2.jpg</picture>
        <picture>https://img/2.jpg, https://img/3.jpg</picture>
        <vendor>Лига Диванов</vendor>
        <name>Диван прямой Милтон</name>
        <description>Описание</description>
        <manufacturer_warranty>true</manufacturer_warranty>
        <country_of_origin>Россия</country_of_origin>
        <barcode>109775</barcode>
        <vendorCode>109775</vendorCode>
        <weight>150</weight>
        <dimensions>320/106/88</dimensions>
        <param name="Цвет">Бежевый</param>
        <param name="Коллекция">Милтон</param>
        <param name="Коллекция">Милтон</param>
        <param name="Ножки">Деревянные</param>
        <param name="Ножки">Пластиковые</param>
      </offer>
      <offer id="2" available="false">
        <price>1000</price>
        <currencyId>RUB</currencyId>
        <categoryId>6632</categoryId>
        <vendorCode>200</vendorCode>
        <name>Недоступный товар</name>
      </offer>
    </offers>
  </shop>
</yml_catalog>
"""


class LigaFeedTests(unittest.TestCase):
    def parse(self, text=XML):
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', suffix='.xml', delete=False) as fh:
            fh.write(text)
            path = fh.name
        try:
            return parse_feed(path)
        finally:
            os.unlink(path)

    def test_parses_categories_offers_images_and_params(self):
        snapshot = self.parse()
        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.categories['6632'].name, 'Прямые диваны')
        self.assertEqual(snapshot.categories['6632'].parent_id, '5317')
        offer = snapshot.offers[0]
        self.assertEqual(offer.kit_sku, 'liga-109775')
        self.assertEqual(offer.vendor_code, '109775')
        self.assertEqual(offer.images, [
            'https://img/1.jpg',
            'https://img/2.jpg',
            'https://img/3.jpg',
        ])
        self.assertEqual(offer.params['Коллекция'], ['Милтон'])
        self.assertEqual(offer.params['Ножки'], ['Деревянные', 'Пластиковые'])
        self.assertEqual(offer.source_url, 'https://ligadivanov.ru/item/109775')
        self.assertEqual(offer.country_of_origin, 'Россия')
        self.assertTrue(offer.manufacturer_warranty)

    def test_available_false_is_preserved(self):
        snapshot = self.parse()
        self.assertFalse(snapshot.offers[1].available)

    def test_missing_vendor_code_is_rejected(self):
        broken = XML.replace('<vendorCode>200</vendorCode>', '')
        with self.assertRaises(ValueError):
            self.parse(broken)

    def test_malformed_xml_does_not_return_partial_snapshot(self):
        with self.assertRaises(Exception):
            self.parse(XML[:-25])


if __name__ == '__main__':
    unittest.main()
