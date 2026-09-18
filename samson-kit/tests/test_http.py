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

class RetryUploadSession:
    def __init__(self):
        self.calls=0
        self.payloads=[]

    def request(self,**kwargs):
        self.calls += 1
        fh=kwargs['files']['file'][1]
        self.payloads.append(fh.read())
        if self.calls == 1:
            response=FakeResponse(429,'retry')
            response.headers={'Retry-After':'0'}
            return response
        response=FakeResponse(200,'json')
        response.json=lambda: {'id':'file-ok'}
        return response


class UploadRetryTests(unittest.TestCase):
    def test_file_body_is_rewound_before_retry(self):
        import io
        session=RetryUploadSession()
        http=SafeSession(session=session,max_attempts=2)
        result=http.request_json(
            'POST',
            'https://example.test/v1/files',
            files={'file':('image.jpg',io.BytesIO(b'non-empty-image'),'image/jpeg')},
        )
        self.assertEqual(result,{'id':'file-ok'})
        self.assertEqual(session.payloads,[b'non-empty-image',b'non-empty-image'])


if __name__=='__main__':
    unittest.main()
