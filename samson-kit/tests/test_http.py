import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from samson_kit.http import SafeSession

class FakeResponse:
    def __init__(self,status_code=204,text=''):
        self.status_code=status_code
        self.ok=200 <= status_code < 300
        self.text=text
        self.headers={}
    def json(self):
        raise ValueError('empty')

class FakeSession:
    def request(self,**kwargs):
        return FakeResponse(204,'')

class HttpTests(unittest.TestCase):
    def test_successful_empty_response_returns_empty_object(self):
        http=SafeSession(session=FakeSession(),max_attempts=1)
        self.assertEqual(http.request_json('POST','https://example.test/bulk',json_body={'items':[]}),{})

if __name__=='__main__':
    unittest.main()
