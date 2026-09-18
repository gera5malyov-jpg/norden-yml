import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from samson_kit.mapper import normalize_sku
from samson_kit.sync import SyncRunner

class ImageSafetyTests(unittest.TestCase):
    def test_prepare_media_fails_if_any_source_image_is_missing(self):
        class FakeHttp:
            def download_to_file(self,url,path):
                if url.endswith('2.jpg'):
                    raise RuntimeError('download failed')
                with open(path,'wb') as fh:
                    fh.write(b'img')
        class FakeKit:
            def upload_image(self,path):
                return {'id':'file1'}
        item=normalize_sku({
            'sku':'1','name':'x','category_id':'1',
            'photo_list':['https://a.test/1.jpg','https://a.test/2.jpg']
        })
        runner=SyncRunner(None,FakeKit(),FakeHttp(),dry_run=False)
        with self.assertRaises(RuntimeError):
            runner._prepare_media(item)

    def test_repair_images_replaces_incomplete_media_with_full_source_set(self):
        class FakeHttp:
            def download_to_file(self,url,path):
                with open(path,'wb') as fh:
                    fh.write(url.encode('utf-8'))
        class FakeKit:
            def __init__(self):
                self.uploaded=[]
                self.updated=[]
                self.media=[{'type':'IMAGE','image_id':'old1'}]
            def upload_image(self,path):
                file_id='file'+str(len(self.uploaded)+1)
                self.uploaded.append(file_id)
                return {'id':file_id}
            def get_variant(self,variant_id):
                return {'id':variant_id,'sku':'SAMS-1','media':self.media}
            def update_variant(self,variant_id,payload):
                self.updated.append((variant_id,payload))
                self.media=list(payload.get('media') or self.media)
                return {'id':variant_id,**payload}
        item=normalize_sku({
            'sku':'1','name':'x','category_id':'1',
            'photo_list':['https://a.test/1.jpg','https://a.test/2.jpg']
        })
        kit=FakeKit()
        runner=SyncRunner(None,kit,FakeHttp(),dry_run=False)
        changed=runner._repair_images_if_incomplete(item,{'id':'variant1','sku':'SAMS-1'})
        self.assertTrue(changed)
        self.assertEqual(len(kit.updated),1)
        media=kit.updated[0][1]['media']
        self.assertEqual(len(media),2)
        self.assertEqual([m['display_sequence'] for m in media],[0,1])

    def test_repair_images_skips_complete_variant(self):
        class FakeKit:
            def get_variant(self,variant_id):
                return {'id':variant_id,'sku':'SAMS-1','media':[
                    {'type':'IMAGE','image_id':'old1'},
                    {'type':'IMAGE','image_id':'old2'},
                ]}
            def update_variant(self,variant_id,payload):
                raise AssertionError('must not update complete media')
        item=normalize_sku({
            'sku':'1','name':'x','category_id':'1',
            'photo_list':['https://a.test/1.jpg','https://a.test/2.jpg']
        })
        runner=SyncRunner(None,FakeKit(),None,dry_run=False)
        self.assertFalse(runner._repair_images_if_incomplete(item,{'id':'variant1','sku':'SAMS-1'}))

if __name__=='__main__':
    unittest.main()
