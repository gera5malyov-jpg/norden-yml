import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from samson_kit.mapper import normalize_sku

class OrderQuantityTests(unittest.TestCase):
    def test_pzk_and_intermediate_define_minimum_and_step(self):
        item=normalize_sku({
            'sku':'105715','name':'x',
            'package_list':[
                {'type':'pzk','value':60},
                {'type':'intermediate','value':20},
            ],
        })
        self.assertEqual(item.min_order_quantity,60)
        self.assertEqual(item.order_step,20)

    def test_pzk_without_intermediate_uses_step_one(self):
        item=normalize_sku({
            'sku':'106519','name':'x',
            'package_list':[{'type':'pzk','value':6}],
        })
        self.assertEqual(item.min_order_quantity,6)
        self.assertEqual(item.order_step,1)

    def test_no_pzk_means_normal_single_piece_ordering(self):
        item=normalize_sku({
            'sku':'1','name':'x',
            'package_list':[{'type':'intermediate','value':20}],
        })
        self.assertEqual(item.min_order_quantity,1)
        self.assertEqual(item.order_step,1)

if __name__=='__main__':
    unittest.main()
