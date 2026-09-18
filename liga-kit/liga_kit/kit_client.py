import mimetypes
import os
import time
import time


def extract_items(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ('items', 'results', 'variants', 'warehouses', 'categories', 'characteristics', 'products'):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    data = payload.get('data')
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        nested = data.get('items')
        if isinstance(nested, list):
            return nested
    for value in payload.values():
        if isinstance(value, list):
            return value
    return []


def extract_total(payload):
    if not isinstance(payload, dict):
        return None
    for key in ('total', 'total_count'):
        value = payload.get(key)
        if isinstance(value, int):
            return value
    meta = payload.get('meta')
    if isinstance(meta, dict):
        for key in ('total', 'total_count'):
            value = meta.get(key)
            if isinstance(value, int):
                return value
    return None


def resolve_exact_warehouse(rows, title):
    wanted = str(title).strip()
    matches = [
        str(row.get('id', '')).strip()
        for row in rows
        if isinstance(row, dict)
        and str(row.get('title', '')).strip() == wanted
        and str(row.get('id', '')).strip()
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f'Expected exactly one KIT warehouse named {wanted!r}, found {len(matches)}'
        )
    return matches[0]


def index_liga_variants(rows):
    buckets = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        sku = str(row.get('sku', '')).strip()
        if not sku.startswith('liga-'):
            continue
        buckets.setdefault(sku, []).append(row)
    return (
        {sku: values[0] for sku, values in buckets.items() if len(values) == 1},
        {sku: values for sku, values in buckets.items() if len(values) > 1},
    )


class KitClient:
    def __init__(self, token, http, base_url='https://api.kit.yandex.net', *, min_request_interval=0.37, monotonic_fn=None, sleep_fn=None):
        self.token = str(token).strip()
        self.http = http
        self.base_url = base_url.rstrip('/')
        self.min_request_interval = max(0.0, float(min_request_interval))
        self._monotonic = monotonic_fn or time.monotonic
        self._sleep = sleep_fn or time.sleep
        self._last_request_at = None
        if not self.token:
            raise ValueError('empty KIT token')

    def _pace(self):
        now = self._monotonic()
        if self._last_request_at is not None:
            delay = self.min_request_interval - (now - self._last_request_at)
            if delay > 0:
                self._sleep(delay)
                now = self._monotonic()
        self._last_request_at = now

    @property
    def headers(self):
        return {'Authorization': f'Bearer {self.token}', 'Accept': 'application/json'}

    def _get(self, path, params=None):
        self._pace()
        return self.http.request_json(
            'GET', self.base_url + path, params=params, headers=self.headers
        )

    def _post(self, path, body=None, files=None):
        self._pace()
        return self.http.request_json(
            'POST', self.base_url + path, headers=self.headers, json_body=body, files=files
        )

    def _patch(self, path, body=None):
        self._pace()
        headers = dict(self.headers)
        headers['Content-Type'] = 'application/merge-patch+json'
        return self.http.request_json(
            'PATCH', self.base_url + path, headers=headers, json_body=body
        )

    def _iter_collection(self, path, params=None):
        page = 1
        seen = 0
        while True:
            query = dict(params or {})
            query.update({'page': page, 'per_page': 100})
            payload = self._get(path, query)
            items = extract_items(payload)
            for item in items:
                if isinstance(item, dict):
                    seen += 1
                    yield item
            total = extract_total(payload)
            if (
                not items
                or (total is not None and seen >= total)
                or (total is None and len(items) < 100)
            ):
                break
            page += 1

    def list_active_warehouses(self):
        return list(self._iter_collection('/v1/warehouses', {'status': 'ACTIVE'}))

    def resolve_warehouse_exact(self, title):
        return resolve_exact_warehouse(self.list_active_warehouses(), title)

    def iter_variants(self, filters=None):
        yield from self._iter_collection('/v1/variants', filters)

    def index_liga_variants(self):
        return index_liga_variants(list(self.iter_variants()))

    def list_categories(self):
        return list(self._iter_collection('/v1/categories', {'status': ['ACTIVE']}))

    def create_category(self, title, parent_id=None):
        body = {'title': str(title).strip()}
        if parent_id:
            body['parent_id'] = str(parent_id)
        return self._post('/v1/categories', body)

    def list_characteristics(self):
        return list(self._iter_collection('/v1/characteristics', {'status': ['ACTIVE']}))

    def create_characteristic(self, title, char_type='STRING', select_mode='SINGLE', unit=None):
        body = {
            'title': str(title).strip(),
            'type': char_type,
            'select_mode': select_mode,
        }
        if unit:
            body['unit'] = str(unit)
        return self._post('/v1/characteristics', body)

    def create_product(self, category_id):
        return self._post('/v1/products', {'category_ids': [str(category_id)]})

    def create_variant(self, payload):
        return self._post('/v1/variants', payload)

    def get_variant(self, variant_id):
        return self._get(f'/v1/variants/{str(variant_id).strip()}')

    def update_variant(self, variant_id, payload):
        return self._patch(f'/v1/variants/{str(variant_id).strip()}', payload)

    def upload_file(self, path):
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        with open(path, 'rb') as fh:
            return self._post(
                '/v1/files',
                files={'file': (os.path.basename(path), fh, mime)},
            )

    def upload_image(self, path):
        return self.upload_file(path)

    def bulk_update_prices(self, items):
        out = []
        for start in range(0, len(items), 5000):
            out.append(
                self._post('/v1/variants/prices/bulk_update', {'items': items[start:start+5000]})
            )
        return out

    def bulk_update_stocks(self, items):
        out = []
        for start in range(0, len(items), 5000):
            out.append(
                self._post('/v1/variants/stocks/bulk_update', {'items': items[start:start+5000]})
            )
        return out
