import unittest

from liga_kit.http import SafeSession


class FakeResponse:
    def __init__(self, status_code=204, text=''):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = text
        self.headers = {}

    def json(self):
        raise ValueError('empty')


class EmptySession:
    def request(self, **kwargs):
        return FakeResponse(204, '')


class ErrorSession:
    def request(self, **kwargs):
        return FakeResponse(400, 'bad token SECRET_TOKEN')

 
class LigaHttpTests(unittest.TestCase):
    def test_successful_empty_response_returns_empty_object(self):
        http = SafeSession(session=EmptySession(), max_attempts=1)
        self.assertEqual(http.request_json('POST', 'https://example.test/bulk', json_body={'items':[]}), {})

    def test_bearer_token_is_redacted_from_error(self):
        http = SafeSession(session=ErrorSession(), max_attempts=1)
        with self.assertRaises(RuntimeError) as ctx:
            http.request_json(
                'GET',
                'https://example.test/v1',
                headers={'Authorization':'Bearer SECRET_TOKEN'},
            )
        self.assertNotIn('SECRET_TOKEN', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
