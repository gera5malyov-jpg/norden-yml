import unittest
from decimal import Decimal

from liga_kit.model import FeedSnapshot, LigaCategory, LigaOffer
from liga_kit.sync import (
    SyncRunner,
    absent_zero_updates,
    build_price_update,
    build_stock_updates,
)


def offer(code='109775', available=True, price='87990'):
    return LigaOffer(
        source_id='115615',
        vendor_code=code,
        kit_sku='liga-' + code,
        available=available,
        category_id='6632',
        name='Диван ' + code,
        description='Описание',
        vendor='Лига Диванов',
        price=Decimal(price) if price is not None else None,
        currency='RUB',
        barcode=code,
        weight='150',
        dimensions='320/106/88',
        source_url='https://example.test/' + code,
        images=[],
        params={},
    )


WAREHOUSES = {'СПБ':'spb-id', 'МСК':'msk-id'}


class LigaSyncRuleTests(unittest.TestCase):
    def test_price_update_uses_feed_price_without_markup_or_discount(self):
        update = build_price_update(
            offer(),
            {'id':'v1','pricing':{'price':'90000','manual_discount_price':'85000'}},
        )
        self.assertEqual(update, {
            'variant_id':'v1',
            'price':'87990.00',
            'manual_discount_price':'87990.00',
        })

    def test_unchanged_price_produces_no_write(self):
        self.assertIsNone(build_price_update(
            offer(),
            {'id':'v1','pricing':{'price':'87990','manual_discount_price':'87990'}},
        ))

    def test_invalid_price_leaves_existing_price_unchanged(self):
        self.assertIsNone(build_price_update(
            offer(price=None),
            {'id':'v1','pricing':{'price':'123','manual_discount_price':'123'}},
        ))

    def test_active_offer_sets_both_managed_warehouses_to_100_only(self):
        variant = {
            'id':'v1',
            'stocks':[
                {'warehouse_id':'spb-id','quantity':0},
                {'warehouse_id':'msk-id','quantity':5},
                {'warehouse_id':'other-id','quantity':77},
            ],
        }
        updates = build_stock_updates(offer(), variant, WAREHOUSES)
        self.assertEqual(
            {(x['warehouse_id'], x['quantity']) for x in updates},
            {('spb-id',100), ('msk-id',100)},
        )
        self.assertNotIn('other-id', {x['warehouse_id'] for x in updates})

    def test_unavailable_offer_sets_both_managed_warehouses_to_zero(self):
        variant = {
            'id':'v1',
            'stocks':[
                {'warehouse_id':'spb-id','quantity':100},
                {'warehouse_id':'msk-id','quantity':100},
            ],
        }
        updates = build_stock_updates(offer(available=False), variant, WAREHOUSES)
        self.assertEqual(
            {(x['warehouse_id'], x['quantity']) for x in updates},
            {('spb-id',0), ('msk-id',0)},
        )

    def test_complete_feed_absence_zeroes_both_managed_warehouses(self):
        index = {
            'liga-1': {
                'id':'v1',
                'stocks':[
                    {'warehouse_id':'spb-id','quantity':100},
                    {'warehouse_id':'msk-id','quantity':100},
                    {'warehouse_id':'other-id','quantity':88},
                ],
            }
        }
        updates = absent_zero_updates(index, set(), WAREHOUSES, complete=True)
        self.assertEqual(
            {(x['warehouse_id'], x['quantity']) for x in updates},
            {('spb-id',0), ('msk-id',0)},
        )
        self.assertNotIn('other-id', {x['warehouse_id'] for x in updates})

    def test_incomplete_feed_never_zeroes_absent_products(self):
        index = {'liga-1': {'id':'v1','stocks':[{'warehouse_id':'spb-id','quantity':100}]}}
        self.assertEqual(
            absent_zero_updates(index, set(), WAREHOUSES, complete=False),
            [],
        )


class ExistingKit:
    def __init__(self):
        self.price_batches = []
        self.stock_batches = []
        self.content_calls = []

    def resolve_warehouse_exact(self, title):
        return WAREHOUSES[title]

    def index_liga_variants(self):
        return ({
            'liga-109775': {
                'id':'v1',
                'sku':'liga-109775',
                'pricing':{'price':'90000','manual_discount_price':'90000'},
                'stocks':[
                    {'warehouse_id':'spb-id','quantity':0},
                    {'warehouse_id':'msk-id','quantity':0},
                ],
            }
        }, {})

    def list_categories(self):
        return []

    def list_characteristics(self):
        return []

    def bulk_update_prices(self, items):
        self.price_batches.append(list(items))

    def bulk_update_stocks(self, items):
        self.stock_batches.append(list(items))

    def update_variant(self, *args, **kwargs):
        self.content_calls.append(('update_variant', args, kwargs))
        raise AssertionError('Existing Liga content must not be rewritten')

    def create_category(self, *args, **kwargs):
        self.content_calls.append(('create_category', args, kwargs))
        raise AssertionError('Existing Liga content must not be rewritten')

    def create_characteristic(self, *args, **kwargs):
        self.content_calls.append(('create_characteristic', args, kwargs))
        raise AssertionError('Existing Liga content must not be rewritten')

    def upload_image(self, *args, **kwargs):
        self.content_calls.append(('upload_image', args, kwargs))
        raise AssertionError('Existing Liga content must not be rewritten')


class NewKit:
    def __init__(self):
        self.created_variant = None

    def resolve_warehouse_exact(self, title):
        return WAREHOUSES[title]

    def index_liga_variants(self):
        return ({}, {})

    def list_categories(self):
        return [{'id':'cat1','title':'Прямые диваны','parent_id':''}]

    def list_characteristics(self):
        return []

    def create_characteristic(self, title, char_type='STRING', select_mode='SINGLE', unit=None):
        return {'id':'ch-' + title, 'title':title, 'type':char_type, 'select_mode':select_mode}

    def create_product(self, category_id):
        self.product_category = category_id
        return {'id':'p1'}

    def create_variant(self, payload):
        self.created_variant = payload
        return {'id':'v-new', **payload}

    def bulk_update_prices(self, items):
        raise AssertionError('No existing price batch expected')

    def bulk_update_stocks(self, items):
        raise AssertionError('No existing stock batch expected')


class LigaSyncRunnerTests(unittest.TestCase):
    def snapshot(self, offers, complete=True):
        return FeedSnapshot(
            categories={'6632':LigaCategory('6632','Прямые диваны',None)},
            offers=offers,
            complete=complete,
        )

    def test_existing_product_updates_only_price_and_stock(self):
        kit = ExistingKit()
        runner = SyncRunner(self.snapshot([offer()]), kit, None, dry_run=False)
        report = runner.run()
        self.assertEqual(kit.content_calls, [])
        self.assertEqual(len(kit.price_batches), 1)
        self.assertEqual(len(kit.price_batches[0]), 1)
        self.assertEqual(
            {(x['warehouse_id'], x['quantity']) for x in kit.stock_batches[0]},
            {('spb-id',100), ('msk-id',100)},
        )
        self.assertEqual(report['price_changes'], 1)
        self.assertEqual(report['spb_stock_changes'], 1)
        self.assertEqual(report['msk_stock_changes'], 1)

    def test_new_product_has_exact_price_and_both_stocks(self):
        kit = NewKit()
        runner = SyncRunner(self.snapshot([offer()]), kit, None, dry_run=False)
        report = runner.run()
        payload = kit.created_variant
        self.assertEqual(payload['sku'], 'liga-109775')
        self.assertEqual(payload['pricing'], {
            'price':'87990.00',
            'manual_discount_price':'87990.00',
        })
        self.assertEqual(
            {(x['warehouse_id'], x['quantity']) for x in payload['stocks']},
            {('spb-id',100), ('msk-id',100)},
        )
        self.assertEqual(report['new_products_created'], 1)

    def test_limited_run_disables_absent_reconciliation(self):
        snapshot = self.snapshot([offer('1'), offer('2')], complete=True)
        kit = ExistingKit()
        runner = SyncRunner(snapshot, kit, None, dry_run=True, max_items=1)
        report = runner.run()
        self.assertFalse(report['catalog_complete'])
        self.assertEqual(report['absent_to_zero'], 0)


if __name__ == '__main__':
    unittest.main()
