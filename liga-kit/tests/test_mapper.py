import unittest
from decimal import Decimal

from liga_kit.mapper import characteristics_from_offer
from liga_kit.model import LigaOffer


def make_offer(**overrides):
    data = dict(
        source_id='115615',
        vendor_code='109775',
        kit_sku='liga-109775',
        available=True,
        category_id='6632',
        name='Диван прямой Милтон',
        description='Описание',
        vendor='Лига Диванов',
        price=Decimal('87990.00'),
        currency='RUB',
        barcode='109775',
        weight='150',
        dimensions='320/106/88',
        source_url='https://ligadivanov.ru/item/109775',
        country_of_origin='Россия',
        manufacturer_warranty=True,
        images=['https://img/1.jpg'],
        params={
            'Цвет':['Бежевый'],
            'Коллекция':['Милтон'],
            'Ножки':['Деревянные', 'Пластиковые'],
        },
    )
    data.update(overrides)
    return LigaOffer(**data)


class LigaMapperTests(unittest.TestCase):
    def test_characteristics_include_feed_params_and_top_level_fields(self):
        chars = characteristics_from_offer(make_offer())
        self.assertIn(('Цвет', ['Бежевый']), chars)
        self.assertIn(('Коллекция', ['Милтон']), chars)
        self.assertIn(('Ножки', ['Деревянные', 'Пластиковые']), chars)
        self.assertIn(('Штрихкод', ['109775']), chars)
        self.assertIn(('Вес', ['150']), chars)
        self.assertIn(('Габариты', ['320/106/88']), chars)
        self.assertIn(('Страна производства', ['Россия']), chars)
        self.assertIn(('Гарантия производителя', ['Да']), chars)
        self.assertIn(('Артикул Liga', ['109775']), chars)

    def test_duplicate_values_are_removed_without_reordering(self):
        offer = make_offer(params={'Коллекция':['Милтон', 'Милтон', 'Milton']})
        chars = characteristics_from_offer(offer)
        self.assertIn(('Коллекция', ['Милтон', 'Milton']), chars)


if __name__ == '__main__':
    unittest.main()
