import io, os, sys, unittest
from unittest.mock import patch
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

class UploadRetrySession:
    def __init__(self):
        self.calls=0
        self.payloads=[]
    def request(self,**kwargs):
        stream=kwargs['files']['file'][1]
        self.payloads.append(stream.read())
        self.calls += 1
        return FakeResponse(500,'busy') if self.calls == 1 else FakeResponse(204,'')

class HttpTests(unittest.TestCase):
    def test_successful_empty_response_returns_empty_object(self):
        http=SafeSession(session=FakeSession(),max_attempts=1)
        self.assertEqual(http.request_json('POST','https://example.test/bulk',json_body={'items':[]}),{})

    def test_file_stream_is_rewound_before_retry(self):
        session=UploadRetrySession()
        http=SafeSession(session=session,max_attempts=2)
        stream=io.BytesIO(b'image-bytes')
        with patch('samson_kit.http.time.sleep', return_value=None):
            result=http.request_json(
                'POST',
                'https://example.test/files',
                files={'file':('image.jpg',stream,'image/jpeg')},
            )
        self.assertEqual(result,{})
        self.assertEqual(session.payloads,[b'image-bytes',b'image-bytes'])

if __name__=='__main__':
    unittest.main()
