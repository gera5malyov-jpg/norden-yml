import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from samson_kit.kit_client import KitClient

class MigrationClientTests(unittest.TestCase):
    def test_update_variant_uses_merge_patch_content_type(self):
        class FakeHttp:
            def __init__(self): self.call=None
            def request_json(self,method,url,**kwargs):
                self.call=(method,url,kwargs)
                return {'id':'variant1','characteristics':kwargs.get('json_body',{}).get('characteristics',[])}
        fake=FakeHttp(); kit=KitClient('token',fake)
        body={'characteristics':[{'characteristic_id':'c1','value':'x','values':['x']}]}
        result=kit.update_variant('variant1',body)
        self.assertEqual(result['id'],'variant1')
        method,url,kwargs=fake.call
        self.assertEqual(method,'PATCH')
        self.assertTrue(url.endswith('/v1/variants/variant1'))
        self.assertEqual(kwargs['json_body'],body)
        self.assertEqual(kwargs['headers']['Content-Type'],'application/merge-patch+json')
        self.assertEqual(kwargs['headers']['Authorization'],'Bearer token')

if __name__=='__main__': unittest.main()
