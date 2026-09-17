from urllib.parse import parse_qs, urlparse

def parse_page(payload):
    envelope = payload[0] if isinstance(payload, list) and len(payload) == 1 else payload
    if not isinstance(envelope, dict) or not isinstance(envelope.get('data'), list):
        raise ValueError('invalid Samson page envelope')
    pagination = (envelope.get('meta') or {}).get('pagination') or {}
    next_url = pagination.get('next')
    if not next_url: return envelope['data'], None
    query = parse_qs(urlparse(next_url).query)
    values = query.get('pagination_page') or []
    if not values or not str(values[0]).isdigit(): raise ValueError('invalid Samson next-page cursor')
    return envelope['data'], int(values[0])

class SamsonClient:
    def __init__(self, api_key, http, base_url='https://api.samsonopt.ru/v1'):
        self.api_key = str(api_key).strip(); self.http = http; self.base_url = base_url.rstrip('/')
        if not self.api_key: raise ValueError('empty Samson API key')
    def _iter(self, path):
        page = 1; seen = set()
        while page is not None:
            if page in seen: raise RuntimeError('Samson pagination loop detected')
            seen.add(page)
            payload = self.http.request_json('GET', self.base_url + path, params={'api_key':self.api_key,'response_format':'json','pagination_page':page}, headers={'Accept':'application/json'})
            items, page = parse_page(payload)
            for item in items:
                if isinstance(item, dict): yield item
    def iter_categories(self): yield from self._iter('/category/')
    def iter_skus(self): yield from self._iter('/sku/')
    def iter_stock(self): yield from self._iter('/sku/stock/')
    def iter_prices(self): yield from self._iter('/sku/price/')
