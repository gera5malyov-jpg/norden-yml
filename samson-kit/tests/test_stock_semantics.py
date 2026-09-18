import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from samson_kit.mapper import normalize_sku, extract_stock_parts
from samson_kit.rules import calculate_stock

class SamsonStockSemanticsTests(unittest.TestCase):
    def test_out_of_stock_flag_does_not_zero_product_still_in_catalog_with_positive_stock(self):
        item=normalize_sku({
            'sku':'106225',
            'name':'x',
            'out_of_stock':1,
            'stock_list':[
                {'type':'idp','value':46},
                {'type':'distribution_warehouse','value':1871},
                {'type':'total','value':1917},
            ],
        })
        self.assertFalse(item.withdrawn)
        self.assertEqual(item.stock_parts,[1917])
        self.assertEqual(calculate_stock(item.stock_parts,active=item.active,withdrawn=item.withdrawn),1917)

    def test_out_of_stock_flag_with_zero_stock_uses_100_while_product_is_in_catalog(self):
        item=normalize_sku({
            'sku':'106999',
            'name':'x',
            'out_of_stock':1,
            'stock_list':[
                {'type':'idp','value':0},
                {'type':'distribution_warehouse','value':0},
                {'type':'total','value':0},
            ],
        })
        self.assertFalse(item.withdrawn)
        self.assertEqual(calculate_stock(item.stock_parts,active=item.active,withdrawn=item.withdrawn),100)

    def test_nested_specific_stock_response_is_parsed(self):
        stock=[
            [
                {'type':'idp','value':18},
                {'type':'transit','value':0},
                {'type':'distribution_warehouse','value':0},
                {'type':'remote_warehouse','value':0},
                {'type':'total','value':18},
            ]
        ]
        self.assertEqual(extract_stock_parts(stock),[18])

    def test_explicit_deleted_flag_still_zeroes_stock(self):
        item=normalize_sku({
            'sku':'1',
            'name':'x',
            'deleted':1,
            'stock_list':[{'type':'total','value':55}],
        })
        self.assertTrue(item.withdrawn)
        self.assertEqual(calculate_stock(item.stock_parts,active=item.active,withdrawn=item.withdrawn),0)

if __name__=='__main__':
    unittest.main()
