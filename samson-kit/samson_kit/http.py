import time
from urllib.parse import urlsplit
import requests

RETRY_STATUSES = {429, 500, 502, 503, 504}


def _capture_file_positions(files):
    positions = []
    if not isinstance(files, dict):
        return positions
    for part in files.values():
        stream = part[1] if isinstance(part, (tuple, list)) and len(part) >= 2 else part
        if not hasattr(stream, 'tell') or not hasattr(stream, 'seek'):
            continue
        try:
            positions.append((stream, stream.tell()))
        except Exception:
            continue
    return positions


def _rewind_files(positions):
    for stream, position in positions:
        stream.seek(position)

def _safe_error_detail(response, headers=None, params=None):
    try:
        text = (response.text or '').strip().replace('\r', ' ').replace('\n', ' ')
    except Exception:
        return ''
    secrets = []
    for value in (headers or {}).values():
        value = str(value or '')
        if value.lower().startswith('bearer '):
            secrets.append(value[7:].strip())
    for key, value in (params or {}).items():
        if str(key).lower() in ('api_key', 'token', 'access_token'):
            secrets.append(str(value or ''))
    for secret in secrets:
        if len(secret) >= 6:
            text = text.replace(secret, '***')
    return text[:1500]

class SafeSession:
    def __init__(self, session=None, max_attempts=4, timeout=(10,60)):
        self.session = session or requests.Session()
        self.max_attempts = max(1, int(max_attempts))
        self.timeout = timeout

    def request_json(self, method, url, *, params=None, headers=None, json_body=None, files=None):
        last_exc = None
        file_positions = _capture_file_positions(files)
        for attempt in range(self.max_attempts):
            _rewind_files(file_positions)
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
                    detail = _safe_error_detail(response, headers, params)
                    suffix = f' :: {detail}' if detail else ''
                    raise RuntimeError(f'HTTP {response.status_code} {method.upper()} {p.scheme}://{p.netloc}{p.path}{suffix}')
                if response.status_code == 204 or not (response.text or '').strip():
                    return {}
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
