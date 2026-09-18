import tempfile
import unittest

from liga_kit.kit_client import KitClient, index_liga_variants, resolve_exact_warehouse


class LigaKitClientTests(unittest.TestCase):
    def test_index_only_claims_liga_prefix(self):
        rows = [
            {'id':'l1','sku':'liga-109775'},
            {'id':'s1','sku':'SAMS-109775'},
            {'id':'n1','sku':'NORMAL-1'},
        ]
        index, duplicates = index_liga_variants(rows)
        self.assertEqual(set(index), {'liga-109775'})
        self.assertEqual(duplicates, {})

    def test_duplicate_liga_sku_is_not_indexed_as_unique(self):
        index, duplicates = index_liga_variants([
            {'id':'a','sku':'liga-1'},
            {'id':'b','sku':'liga-1'},
        ])
        self.assertNotIn('liga-1', index)
        self.assertEqual(len(duplicates['liga-1']), 2)

    def test_resolve_exact_warehouse(self):
        rows = [{'id':'spb','title':'СПБ'}, {'id':'msk','title':'МСК'}]
        self.assertEqual(resolve_exact_warehouse(rows, 'СПБ'), 'spb')
        self.assertEqual(resolve_exact_warehouse(rows, 'МСК'), 'msk')

    def test_ambiguous_warehouse_fails(self):
        with self.assertRaises(RuntimeError):
            resolve_exact_warehouse([
                {'id':'a','title':'СПБ'},
                {'id':'b','title':'СПБ'},
            ], 'СПБ')

    def test_upload_image_sends_mime_type(self):
        class FakeHttp:
            def request_json(self, method, url, **kwargs):
                self.files = kwargs.get('files')
                return {'id':'file1'}
        fake = FakeHttp()
        kit = KitClient('token', fake)
        with tempfile.NamedTemporaryFile(suffix='.jpg') as fh:
            fh.write(b'jpeg')
            fh.flush()
            kit.upload_image(fh.name)
        self.assertEqual(fake.files['file'][2], 'image/jpeg')


if __name__ == '__main__':
    unittest.main()
