import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / 'fixtures'
def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result); return result
def patcher():
    if not (ROOT / 'stage56_delivery.py').exists(): return None
    return module('stage56_delivery')
def source(name): return (FIXTURES / name).read_bytes().decode('utf-8')
class DeliveryTests(unittest.TestCase):
    def setUp(self): self.p = patcher()
    def test_product_replaces_existing_notice_without_touching_purchase_or_features(self):
        old = source('delivery-product-stage54.html')
        self.assertIsNotNone(self.p, 'Delivery patch builder is missing')
        new = self.p.patch_product(old)
        self.assertEqual(new.count('<section class="megapolis-purchase-trust"'), 1)
        self.assertIn('из разных городов', new)
        self.assertEqual(new.replace(self.p.PRODUCT_NEW,self.p.PRODUCT_OLD), old)
        self.assertRaises(ValueError, self.p.patch_product, new)
    def test_order_adds_one_notice_beside_native_form_without_changing_checkout(self):
        old = source('delivery-order.html'); self.assertIsNotNone(self.p, 'Delivery patch builder is missing')
        new = self.p.patch_order(old)
        self.assertEqual(new.count('id="megapolis-delivery-notice"'),1)
        self.assertEqual(new.replace(self.p.ORDER_NOTICE,''),old)
        self.assertIn('$wa->shop->checkout()->form',new)
        self.assertIn("$wa->domainUrl() == 'https://profikompany.ru'",new)
        self.assertRaises(ValueError,self.p.patch_order,new)
    def test_cms_removes_blocking_calculator_preserving_all_surrounding_delivery_terms(self):
        old=source('delivery-page12.html')
        self.assertIsNotNone(self.p,'Delivery patch builder is missing'); new=self.p.patch_delivery_content(old)
        self.assertNotIn('от 600 ₽',new); self.assertIn('города отправки',new)
        self.assertNotIn('transportnye-kompanii',new)
        self.assertNotIn('calculatorBuilder',new)
        self.assertNotIn('cityFrom',new)
        self.assertEqual(new.count('id="megapolis-delivery-calculator-note"'),1)
        self.assertIn('Для расчёта нужны выбранные товары и адрес получения.',new)
        self.assertIn('Менеджер уточнит город отправления и согласует стоимость доставки отдельно.',new)
        for company in ('ПЭК','Деловые линии','СДЭК','DPD','Байкал Сервис'):
            self.assertIn('<strong>'+company+'</strong>',new)
        self.assertIn('после 100% предоплаты',new)
        self.assertIn('Все отправленные заказы застрахованы на сумму заказанного изделия.',new)
        # Invert only the three authorized substitutions: all other bytes survive.
        restored=new.replace(self.p.CMS_PRICE_NEW,self.p.CMS_PRICE_OLD)
        restored=restored.replace(self.p.CMS_FACTORS_NEW,self.p.CMS_FACTORS_OLD)
        block_start=old.index('<div id="transportnye-kompanii">')
        block_end=old.index('</script>',old.index('<script>calculatorBuilder'))+len('</script>')
        restored=restored.replace(self.p.CMS_WIDGET_NOTE,old[block_start:block_end])
        self.assertEqual(restored,old)
        self.assertRaises(ValueError,self.p.patch_delivery_content,new)
    def test_changed_sources_fail_closed(self):
        self.assertIsNotNone(self.p,'Delivery patch builder is missing')
        for fn,old in [(self.p.patch_product,source('delivery-product-stage54.html')),
                       (self.p.patch_order,source('delivery-order.html'))]:
            self.assertRaises(ValueError,fn,old+'changed')
        old=source('delivery-order.html').replace('id="js-order-form"','id="different"')
        self.assertRaises(ValueError,self.p.patch_order,old,expected_sha256=hashlib.sha256(old.encode()).hexdigest())
if __name__=='__main__': unittest.main()
