"""Safety contracts for factual, bounded content proposals (no network/DB)."""
import copy
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'stage55_seo_content.py'

def module():
    spec = importlib.util.spec_from_file_location('seo_content', MODULE)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def category():
    return dict(id='285', name='Стулья обеденные', full_url='stul/stulya',
                meta_title='', meta_description=None, products='8')


def product():
    return dict(id='12', name='Стул «Модель»', url='stul-model', category_id='285',
                category_url='stul/stulya', price='2500', count='3', status='1',
                description=None, features={'vendor':'Фабрика', 'material':'Дерево',
                'color':{'1':'Белый'}, 'kollektsiya':'Серия', 'shirina':'45'},
                skus=[dict(available='1', count='3', price='2500')])


class Safety(unittest.TestCase):
    def test_preserves_existing_metadata_and_requires_verified_route(self):
        m=module(); c=category(); c['meta_title']='Existing'; original=copy.deepcopy(c)
        result=m.build_category_proposals([c], [])
        self.assertEqual(result[0]['set'], {'meta_description': result[0]['set']['meta_description']})
        self.assertEqual(c, original)
        c['full_url']='elsewhere'
        self.assertEqual(m.build_category_proposals([c], []), [])

    def test_paid_signal_precedes_inventory_without_profit_claim(self):
        m=module(); small=category(); big=dict(id='440',name='Столы письменные',
             full_url='stoly_1/stoly-pismennye',meta_title='',meta_description='',products='100')
        result=m.build_category_proposals([big,small], [dict(category_id='285',paid_orders='2',item_revenue='100')])
        self.assertEqual(result[0]['id'],285)
        self.assertEqual(result[0]['evidence']['paid_orders_90d'],2)
        self.assertNotIn('profit', str(result))

    def test_description_escapes_values_and_never_guesses_units(self):
        m=module(); p=product(); p['features']['material']='<script>wood</script>'
        result=m.build_product_proposals([p], [], category_rows=[category()])
        self.assertEqual(len(result),1)
        html=result[0]['set']['description']
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;script&gt;',html)
        self.assertNotIn('Ширина',html)
        self.assertNotIn('наличии',html)
        self.assertEqual(result[0]['expected']['description'],None)
        self.assertEqual(set(result[0]['set']),{'description'})

    def test_missing_spec_existing_description_unknown_category_and_unavailable_skip(self):
        m=module(); variants=[]
        for changes in ({'description':'Already'}, {'category_id':None}, {'count':'0'},
                        {'price':'0'}, {'status':'0'}, {'features':{'vendor':'A'}},
                        {'skus':[dict(available='0',count='2',price='5')]}):
            p=product(); p.update(changes); variants.append(p)
        self.assertEqual(m.build_product_proposals(variants, [], category_rows=[category()]),[])

    def test_orderable_unknown_stock_does_not_make_stock_claim(self):
        m=module();p=product();p['count']=None;p['skus'][0]['count']=None
        result=m.build_product_proposals([p], [], category_rows=[category()])
        self.assertEqual(len(result),1)
        self.assertIsNone(result[0]['expected']['count'])
        self.assertNotIn('налич',result[0]['set']['description'])

    def test_explicit_units_only_no_conversion_of_untyped_numbers(self):
        m=module();p=product()
        definitions=[dict(code='shirina',name='Ширина, см',type='double',default_unit='')]
        result=m.build_product_proposals([p],definitions,category_rows=[category()])
        self.assertIn('Ширина: 45 см',result[0]['set']['description'])
        definitions[0]['name']='Ширина'
        self.assertNotIn('Ширина',m.build_product_proposals([p],definitions,category_rows=[category()])[0]['set']['description'])
        definitions[0].update(type='dimension.length',default_unit='mm')
        self.assertIn('Ширина: 45 мм',m.build_product_proposals([p],definitions,category_rows=[category()])[0]['set']['description'])

    def test_representative_selection_prefers_recorded_stock(self):
        m=module();c=category();other=dict(c,id='271',full_url='stoly_1/stoly')
        rows=[]
        for i in range(8):
            p=product();p['id']=str(i+1);rows.append(p)
        p=product();p.update(id='20',category_id='271',category_url='stoly_1/stoly');rows.append(p)
        unknown=product();unknown['id']='21';unknown['count']=None;unknown['skus'][0]['count']=None;rows.insert(0,unknown)
        result=m.build_product_proposals(rows,[],category_rows=[c,other],limit=4)
        self.assertEqual([r['id'] for r in result],[1,20,2,3])

    def test_feature_metadata_objects_are_not_customer_specs(self):
        m=module();p=product();p['features']['color']={'1':{'feature_id':'12','sort':'3'}}
        self.assertEqual(m.build_product_proposals([p],[],category_rows=[category()]),[])

    def test_sitemap_duplicate_patch_is_scoped_and_preserves_paging_counter(self):
        m=module()
        source=m.SITEMAP_INIT_ANCHOR+'\n'+m.SITEMAP_PRODUCT_ANCHOR+'\n'+m.SITEMAP_PAGE_ANCHOR
        patched=m.build_sitemap_duplicate_patch(source)
        self.assertIn("=== 'profikompany.ru'",patched)
        self.assertEqual(patched.count('$count_adding++;'),2)
        self.assertEqual(patched.count('$this->addUrl('),2)
        with self.assertRaises(ValueError):m.build_sitemap_duplicate_patch(patched)
        with self.assertRaises(ValueError):m.build_sitemap_duplicate_patch(source.replace(m.SITEMAP_INIT_ANCHOR,''))

    def test_bounded_and_cas_payload_excludes_private_fields(self):
        m=module(); rows=[]
        for i in range(60):
            p=product();p['id']=str(i+1);p['purchase_price']='SECRET';rows.append(p)
        result=m.build_product_proposals(rows, [], category_rows=[category()])
        self.assertEqual(len(result),50)
        self.assertNotIn('SECRET',str(result))
        self.assertIn('features_sha256',result[0]['expected'])
        self.assertEqual(m.build_product_proposals(rows, [], category_rows=[category()],limit=500),result)

if __name__=='__main__':unittest.main()
