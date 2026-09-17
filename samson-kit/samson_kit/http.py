import time
from urllib.parse import urlsplit
import requests

RETRY_STATUSES = {429, 500, 502, 503, 504}

class SafeSession:
    def __init__(self, session=None, max_attempts=4, timeout=(10,60)):
        self.session = session or requests.Session()
        self.max_attempts = max(1, int(max_attempts))
        self.timeout = timeout

    def request_json(self, method, url, *, params=None, headers=None, json_body=None, files=None):
        last_exc = None
        for attempt in range(self.max_attempts):
            try:
                response = self.session.request(method=method,url=url,params=params,headers=headers,json=json_body,files=files,timeout=self.timeout)
                if response.status_code in RETRY_STATUSES and attempt + 1 < self.max_attempts:
                    retry_after = response.headers.get('Retry-After')
                    delay = 2 ** attempt
                    if retry_after and retry_after.isdigit(): delay = max(delay, int(retry_after))
                    time.sleep(min(delay, 30))
                    continue
                if not response.ok:
                    p = urlsplit(url)
                    raise RuntimeError(f'HTTP {response.status_code} {method.upper()} {p.scheme}://{p.netloc}{p.path}')
                try:
                    return response.json()
                except ValueError as exc:
                    p = urlsplit(url)
                    raise RuntimeError(f'Invalid JSON from {p.scheme}://{p.netloc}{p.path}') from exc
            except requests.RequestException as exc:
                last_exc = exc
                if attempt + 1 >= self.max_attempts: break
                time.sleep(min(2 ** attempt, 30))
        p = urlsplit(url)
        raise RuntimeError(f'HTTP transport failure {method.upper()} {p.scheme}://{p.netloc}{p.path}') from last_exc

    def download_to_file(self, url, path):
        last_exc = None
        for attempt in range(self.max_attempts):
            try:
                response = self.session.get(url, stream=True, timeout=self.timeout)
                if response.status_code in RETRY_STATUSES and attempt + 1 < self.max_attempts:
                    time.sleep(min(2 ** attempt, 30)); continue
                if not response.ok:
                    p = urlsplit(url)
                    raise RuntimeError(f'HTTP {response.status_code} GET {p.scheme}://{p.netloc}{p.path}')
                with open(path, 'wb') as fh:
                    for chunk in response.iter_content(chunk_size=1024 * 128):
                        if chunk: fh.write(chunk)
                return path
            except requests.RequestException as exc:
                last_exc = exc
                if attempt + 1 >= self.max_attempts: break
                time.sleep(min(2 ** attempt, 30))
        p = urlsplit(url)
        raise RuntimeError(f'HTTP transport failure GET {p.scheme}://{p.netloc}{p.path}') from last_exc
