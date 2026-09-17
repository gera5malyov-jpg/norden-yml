import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from samson_kit.mapper import normalize_sku

class DocumentSourceTests(unittest.TestCase):
    def test_certificate_extended_url_list_is_imported(self):
        item=normalize_sku({
            'sku':'2','name':'x','category_id':'1',
            'certificate_extended_list':[
                {
                    'name':'Сертификат соответствия',
                    'issued_by':'ОС',
                    'active_to':'04.02.2030',
                    'url_list':['https://a.test/page-1.jpg','https://a.test/page-2.jpg'],
                }
            ],
        })
        self.assertEqual(item.document_urls,['https://a.test/page-1.jpg','https://a.test/page-2.jpg'])
        titles=[title for title,_ in item.characteristics]
        self.assertNotIn('Сертификаты',titles)
        self.assertNotIn('Расширенные сертификаты',titles)

if __name__=='__main__': unittest.main()
