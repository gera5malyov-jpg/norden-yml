import os, sys, unittest
from decimal import Decimal
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from samson_kit.rules import calculate_prices, calculate_stock, to_kit_sku
from samson_kit.samson_client import parse_page
from samson_kit.kit_client import resolve_exact_warehouse, index_samson_variants
from samson_kit.mapper import normalize_sku, category_chain
from samson_kit.sync import build_price_update, build_stock_update, absent_zero_updates

class CoreTests(unittest.TestCase):
    def test_sku_prefix(self): self.assertEqual(to_kit_sku('531863'),'SAMS-531863')
    def test_price_thresholds(self):
        p=calculate_prices(Decimal('3000.00')); self.assertEqual((p.sale,p.old,p.minimum),(Decimal('4200.00'),Decimal('5400.00'),Decimal('3600.00'))); self.assertEqual(calculate_prices(Decimal('3000.01')).sale,Decimal('3780.01'))
    def test_stock_rules(self):
        self.assertEqual(calculate_stock([2,3,4],active=True,withdrawn=False),9); self.assertEqual(calculate_stock([0,0],active=True,withdrawn=False),100); self.assertEqual(calculate_stock([5],active=False,withdrawn=True),0); self.assertIsNone(calculate_stock(None,active=True,withdrawn=False))
    def test_samson_page(self):
        data,nxt=parse_page([{'data':[{'sku':'1'}],'meta':{'pagination':{'next':'https://api.samsonopt.ru/v1/sku/?pagination_page=2&api_key=SECRET'}}}]); self.assertEqual(data[0]['sku'],'1'); self.assertEqual(nxt,2)
    def test_warehouse_exact(self): self.assertEqual(resolve_exact_warehouse([{'id':'x','title':'СПБ'}],'СПБ'),'x')
    def test_duplicate_samson_index(self):
        idx,dup=index_samson_variants([{'id':'1','sku':'SAMS-1'},{'id':'2','sku':'SAMS-2'},{'id':'3','sku':'SAMS-2'},{'id':'4','sku':'ABC'}]); self.assertEqual(set(idx),{'SAMS-1'}); self.assertEqual(set(dup),{'SAMS-2'})
    def test_mapper(self):
        item=normalize_sku({'sku':'531863','name':'Товар','category_id':'3','price':'2500','stocks':[{'quantity':4},{'quantity':6}],'barcodes':['4601'],'characteristics':[{'name':'Цвет','value':'Белый'}]}); self.assertEqual(item.stock_parts,[4,6]); self.assertEqual(item.purchase_price,Decimal('2500')); self.assertIn(('Цвет',['Белый']),item.characteristics)
    def test_category_chain(self):
        cats={'1':{'id':'1','name':'R','parent_id':None},'2':{'id':'2','name':'C','parent_id':'1'}}; self.assertEqual([x['name'] for x in category_chain('2',cats)],['R','C'])
    def test_price_payload_kit_semantics(self):
        item=normalize_sku({'sku':'1','name':'x','price':'2500','stock':1}); v={'id':'v','pricing':{'price':'4500.00','manual_discount_price':'3499.00'}}; self.assertEqual(build_price_update(item,v),{'variant_id':'v','price':'4500.00','manual_discount_price':'3500.00'})
    def test_stock_and_absent_safety(self):
        item=normalize_sku({'sku':'1','name':'x','price':'1','stock':0}); self.assertEqual(build_stock_update(item,{'id':'v','stocks':[{'warehouse_id':'spb','quantity':0}]},'spb')['quantity'],100); idx={'SAMS-1':{'id':'v1','stocks':[{'warehouse_id':'spb','quantity':8}]},'SAMS-2':{'id':'v2','stocks':[{'warehouse_id':'spb','quantity':5}]}}; self.assertEqual(absent_zero_updates(idx,{'SAMS-1'},'spb',complete=False),[]); self.assertEqual(absent_zero_updates(idx,{'SAMS-1'},'spb',complete=True)[0]['quantity'],0)

if __name__=='__main__': unittest.main()
