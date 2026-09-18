import os
import unittest
from decimal import Decimal

from liga_kit.model import LigaCategory, LigaOffer
from liga_kit.sync import SyncRunner


def make_offer(images=None, params=None):
    return LigaOffer(
        source_id='115615',
        vendor_code='109775',
        kit_sku='liga-109775',
        available=True,
        category_id='6632',
        name='Диван',
        description='Описание',
        vendor='Лига Диванов',
        price=Decimal('87990.00'),
        currency='RUB',
        barcode='109775',
        weight='150',
        dimensions='320/106/88',
        source_url='https://example.test/item',
        country_of_origin='Россия',
        manufacturer_warranty=True,
        images=images or [],
        params=params or {},
    )


class FakeHttp:
    def __init__(self, fail_url=None):
        self.fail_url = fail_url

    def download_to_file(self, url, path):
        if url == self.fail_url:
            raise RuntimeError('download failed')
        with open(path, 'wb') as fh:
            fh.write(url.encode('utf-8'))
        return path


class FakeKit:
    def __init__(self):
        self.uploaded = []
        self.created_categories = []
        self.created_characteristics = []

    def upload_image(self, path):
        file_id = 'file-' + str(len(self.uploaded) + 1)
        self.uploaded.append(file_id)
        return {'id':file_id}

    def create_category(self, title, parent_id=None):
        row = {'id':'cat-' + str(len(self.created_categories) + 1), 'title':title, 'parent_id':parent_id or ''}
        self.created_categories.append(row)
        return row

    def create_characteristic(self, title, char_type='STRING', select_mode='SINGLE', unit=None):
        row = {'id':'ch-' + str(len(self.created_characteristics) + 1), 'title':title, 'type':char_type, 'select_mode':select_mode}
        self.created_characteristics.append(row)
        return row


class LigaMediaAndMappingTests(unittest.TestCase):
    def test_prepare_media_fails_if_any_source_image_fails(self):
        offer = make_offer(images=['https://img/1.jpg', 'https://img/2.jpg'])
        runner = SyncRunner(None, FakeKit(), FakeHttp(fail_url='https://img/2.jpg'), dry_run=False)
        with self.assertRaises(RuntimeError) as ctx:
            runner._prepare_media(offer)
        self.assertIn('prepared 1 of 2', str(ctx.exception))

    def test_prepare_media_preserves_order(self):
        offer = make_offer(images=['https://img/1.jpg', 'https://img/2.jpg'])
        runner = SyncRunner(None, FakeKit(), FakeHttp(), dry_run=False)
        media = runner._prepare_media(offer)
        self.assertEqual(len(media), 2)
        self.assertEqual([m['display_sequence'] for m in media], [0, 1])

    def test_category_chain_is_created_in_parent_order(self):
        kit = FakeKit()
        runner = SyncRunner(None, kit, FakeHttp(), dry_run=False)
        runner.kit_categories = []
        categories = {
            '5317': LigaCategory('5317', 'Диваны', None),
            '6632': LigaCategory('6632', 'Прямые диваны', '5317'),
        }
        cat_id = runner._ensure_category(make_offer(), categories)
        self.assertEqual(cat_id, 'cat-2')
        self.assertEqual([x['title'] for x in kit.created_categories], ['Диваны', 'Прямые диваны'])

    def test_ambiguous_characteristic_is_skipped_not_guessed(self):
        kit = FakeKit()
        runner = SyncRunner(None, kit, FakeHttp(), dry_run=False)
        runner.kit_characteristics = [
            {'id':'a','title':'Цвет','type':'STRING'},
            {'id':'b','title':'Цвет','type':'STRING'},
        ]
        chars = runner._ensure_characteristics(make_offer(params={'Цвет':['Бежевый']}))
        chosen_ids = {row['characteristic_id'] for row in chars}
        self.assertNotIn('a', chosen_ids)
        self.assertNotIn('b', chosen_ids)
        self.assertEqual(runner.report['warning_count'], 1)


if __name__ == '__main__':
    unittest.main()
