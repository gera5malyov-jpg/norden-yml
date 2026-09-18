# Liga Divanov → Yandex KIT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an isolated weekly Liga Divanov YML → Yandex KIT integration that creates `liga-*` products with full initial content, then updates only feed price and stock 100/100 on СПБ/МСК, with safe zeroing for unavailable or confirmed-absent offers.

**Architecture:** Add a self-contained Python package under `liga-kit/` with its own feed parser, models, KIT client, mapper, sync orchestrator, report writer and tests. The package does not import Samson business logic. Webasyst is extended so `liga-*` joins `SAMS-*` as an externally managed SKU namespace before the first full Liga live import.

**Tech Stack:** Python 3.12, standard-library `xml.etree.ElementTree`, `requests`, `unittest`, GitHub Actions, existing Yandex KIT REST API, PHP/Webasyst Shop-Script plugin for ownership exclusion.

**Spec:** `docs/superpowers/specs/2026-09-18-liga-kit-sync-design.md`

## Global Constraints

- Source feed: `https://ligadivanov.ru/acrit.export/ym_vendormodel_2023.xml`.
- Source business key: `vendorCode`.
- KIT SKU: exact prefix `liga-`; example `109775` → `liga-109775`.
- Active Liga offer stock: exactly 100 on warehouse `СПБ` and exactly 100 on warehouse `МСК`.
- `available="false"` or confirmed absence from a complete feed: 0 on `СПБ` and 0 on `МСК`.
- No other KIT warehouse may be modified by Liga.
- Source price is copied from `<price>` without markup.
- Existing `liga-*` products update only price and the two managed stocks.
- Existing `liga-*` content fields are not rewritten by normal weekly sync.
- All source images must be prepared for a new product; partial image success is not accepted.
- Absent-item zeroing is disabled unless the complete feed is downloaded and parsed successfully.
- `SAMS-*`, ordinary Webasyst SKUs, and other suppliers are never modified by Liga.
- Weekly schedule: Monday 10:00 Moscow = Monday 07:00 UTC = cron `0 7 * * 1`.
- Do not commit the source XML, downloaded images, tokens, or large run artifacts.
- Use `YANDEX_KIT_TOKEN` only from GitHub Actions Secrets.

---

### Task 1: Extend Webasyst external-SKU ownership to `liga-*`

**Files:**
- Restore/use plugin source tree: `yandexkitsync/`
- Modify: `yandexkitsync/lib/classes/shopYandexkitsyncSkuPolicy.class.php`
- Modify: `yandexkitsync/tests/samson_sku_policy.php`
- Modify: `yandexkitsync/lib/config/plugin.php`
- Modify: `yandexkitsync/README.md`
- Create artifact: `yandexkitsync-v0.9.7.zip`

**Interfaces:**
- Consumes: existing v0.9.6 external ownership guards already calling `shopYandexkitsyncSkuPolicy::isExternallyManaged($sku)`.
- Produces: one policy that returns true for exact case-sensitive prefixes `SAMS-` and `liga-`.
- No planner, worker, sync, image or KIT-ID class gets new prefix-specific code; all existing guard call sites keep using the central policy.

- [ ] **Step 1: Extend the policy regression test first**

Add these assertions to the existing standalone policy test:

```php
check(shopYandexkitsyncSkuPolicy::isExternallyManaged('SAMS-531863') === true, 'SAMS prefix must stay external');
check(shopYandexkitsyncSkuPolicy::isExternallyManaged('liga-109775') === true, 'Liga prefix must be external');
check(shopYandexkitsyncSkuPolicy::isExternallyManaged('  liga-109775  ') === true, 'Liga whitespace must be ignored');
check(shopYandexkitsyncSkuPolicy::isExternallyManaged('LIGA-109775') === false, 'Liga prefix match is exact and case-sensitive');
check(shopYandexkitsyncSkuPolicy::isExternallyManaged('liga109775') === false, 'Liga prefix requires hyphen');
check(shopYandexkitsyncSkuPolicy::isExternallyManaged('AF-31622523') === false, 'Normal site SKU must stay managed');
```

- [ ] **Step 2: Run the policy test and verify RED**

Run:

```bash
php yandexkitsync/tests/samson_sku_policy.php
```

Expected: failure on `liga-109775`, because v0.9.6 recognizes only `SAMS-`.

- [ ] **Step 3: Replace the single-prefix policy with a prefix list**

Implement:

```php
<?php

class shopYandexkitsyncSkuPolicy
{
    private static $external_prefixes = array('SAMS-', 'liga-');

    public static function isExternallyManaged($sku)
    {
        $sku = trim((string) $sku);
        if ($sku === '') {
            return false;
        }

        foreach (self::$external_prefixes as $prefix) {
            if (strncmp($sku, $prefix, strlen($prefix)) === 0) {
                return true;
            }
        }

        return false;
    }
}
```

- [ ] **Step 4: Run the complete plugin regression suite**

Run:

```bash
for test in yandexkitsync/tests/*.php; do php "$test" || exit 1; done
```

Expected: every test exits 0; existing `SAMS-*` exclusion remains green and new `liga-*` assertions pass.

- [ ] **Step 5: Release plugin v0.9.7**

Set in `yandexkitsync/lib/config/plugin.php`:

```php
'version' => '0.9.7',
```

Add README ownership note:

```text
Externally managed SKU namespaces: SAMS-* and liga-*.
The plugin ignores these SKUs before matching, creation, price, stock,
image, KIT-ID and missing-on-site reconciliation.
```

Package:

```bash
zip -r yandexkitsync-v0.9.7.zip yandexkitsync -x '*.DS_Store' -x '__MACOSX/*'
```

Verify:

```bash
unzip -p yandexkitsync-v0.9.7.zip yandexkitsync/lib/config/plugin.php | grep "0.9.7"
unzip -p yandexkitsync-v0.9.7.zip yandexkitsync/lib/classes/shopYandexkitsyncSkuPolicy.class.php | grep "liga-"
zipgrep -Ei 'yakit_|Authorization:[[:space:]]*Bearer[[:space:]]+[A-Za-z0-9_-]{20,}' yandexkitsync-v0.9.7.zip && exit 1 || true
```

- [ ] **Step 6: Commit plugin release metadata/source if the source tree is tracked**

```bash
git add yandexkitsync
git commit -m "fix: exclude Liga SKUs from Webasyst KIT sync"
```

**Release gate:** Do not run the full Liga live import until v0.9.7 is installed and a normal Webasyst sync confirms that `liga-*` is not queued for creation, pricing, stock, image, KIT-ID, or missing-on-site zeroing.

---

### Task 2: Build the Liga feed parser and normalized offer model with TDD

**Files:**
- Create: `liga-kit/liga_kit/__init__.py`
- Create: `liga-kit/liga_kit/model.py`
- Create: `liga-kit/liga_kit/feed.py`
- Create: `liga-kit/liga_kit/rules.py`
- Create: `liga-kit/tests/test_feed.py`
- Create: `liga-kit/tests/test_rules.py`

**Interfaces:**
- Produces:
  - `LigaOffer`
  - `FeedSnapshot`
  - `parse_feed(path: str) -> FeedSnapshot`
  - `to_kit_sku(vendor_code: str) -> str`
  - `desired_stock(available: bool) -> int`
  - `normalize_price(value) -> Decimal | None`
- Consumed by mapper and sync tasks.

- [ ] **Step 1: Write failing tests for SKU, price and stock**

```python
from decimal import Decimal
from liga_kit.rules import to_kit_sku, normalize_price, desired_stock

def test_liga_sku_prefix():
    assert to_kit_sku('109775') == 'liga-109775'

def test_price_is_feed_price_without_markup():
    assert normalize_price('87990') == Decimal('87990.00')

def test_active_offer_stock_is_100():
    assert desired_stock(True) == 100

def test_unavailable_offer_stock_is_zero():
    assert desired_stock(False) == 0
```

Use `unittest.TestCase` in the committed test file so the repository stays consistent with the Samson suite.

- [ ] **Step 2: Write failing feed parsing tests using a tiny fixture string**

The fixture must include comma-separated images, multiple `picture` nodes, duplicate images, repeated params and one unavailable offer:

```python
XML = """<?xml version="1.0" encoding="utf-8"?>
<yml_catalog>
  <shop>
    <categories>
      <category id="6632">Прямые диваны</category>
    </categories>
    <offers>
      <offer id="115615" available="true" group_id="6632">
        <price>87990</price>
        <currencyId>RUB</currencyId>
        <categoryId>6632</categoryId>
        <picture>https://img/1.jpg,https://img/2.jpg</picture>
        <picture>https://img/2.jpg, https://img/3.jpg</picture>
        <vendor>Лига Диванов</vendor>
        <name>Диван прямой Милтон</name>
        <description>Описание</description>
        <barcode>109775</barcode>
        <vendorCode>109775</vendorCode>
        <weight>150</weight>
        <dimensions>320/106/88</dimensions>
        <param name="Цвет">Бежевый</param>
        <param name="Коллекция">Милтон</param>
        <param name="Коллекция">Милтон</param>
      </offer>
      <offer id="2" available="false">
        <price>1000</price>
        <categoryId>6632</categoryId>
        <vendorCode>200</vendorCode>
        <name>Недоступный товар</name>
      </offer>
    </offers>
  </shop>
</yml_catalog>"""
```

Assertions:

```python
self.assertTrue(snapshot.complete)
self.assertEqual(snapshot.categories['6632'].name, 'Прямые диваны')
self.assertEqual(snapshot.offers[0].kit_sku, 'liga-109775')
self.assertEqual(snapshot.offers[0].images, [
    'https://img/1.jpg',
    'https://img/2.jpg',
    'https://img/3.jpg',
])
self.assertEqual(snapshot.offers[0].params['Коллекция'], ['Милтон'])
self.assertFalse(snapshot.offers[1].available)
```

- [ ] **Step 3: Run the new tests and verify RED**

Run:

```bash
PYTHONPATH=liga-kit python -m unittest discover -s liga-kit/tests -v
```

Expected: import/module failures because `liga_kit` does not exist yet.

- [ ] **Step 4: Implement `model.py`**

```python
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

@dataclass(frozen=True)
class LigaCategory:
    source_id: str
    name: str
    parent_id: Optional[str] = None

@dataclass(frozen=True)
class LigaOffer:
    source_id: str
    vendor_code: str
    kit_sku: str
    available: bool
    category_id: Optional[str]
    name: str
    description: str
    vendor: str
    price: Optional[Decimal]
    currency: str
    barcode: str
    weight: str
    dimensions: str
    source_url: str
    images: list[str] = field(default_factory=list)
    params: dict[str, list[str]] = field(default_factory=dict)

@dataclass(frozen=True)
class FeedSnapshot:
    categories: dict[str, LigaCategory]
    offers: list[LigaOffer]
    complete: bool
```

- [ ] **Step 5: Implement `rules.py`**

```python
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

MONEY = Decimal('0.01')

def to_kit_sku(vendor_code):
    code = str(vendor_code or '').strip()
    if not code:
        raise ValueError('Liga vendorCode is missing')
    return 'liga-' + code

def normalize_price(value):
    try:
        price = Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if price <= 0:
        return None
    return price.quantize(MONEY, rounding=ROUND_HALF_UP)

def desired_stock(available):
    return 100 if bool(available) else 0
```

- [ ] **Step 6: Implement `feed.py` using `xml.etree.ElementTree.iterparse`**

Key helpers:

```python
def _bool_available(value):
    return str(value or '').strip().lower() == 'true'

def _split_images(elements):
    out = []
    seen = set()
    for element in elements:
        for part in (element.text or '').split(','):
            url = part.strip()
            if url and url not in seen:
                seen.add(url)
                out.append(url)
    return out

def _params(offer):
    grouped = {}
    for node in offer.findall('param'):
        name = str(node.attrib.get('name') or '').strip()
        value = str(node.text or '').strip()
        if not name or not value:
            continue
        values = grouped.setdefault(name, [])
        if value not in values:
            values.append(value)
    return grouped
```

`parse_feed(path)` must:
- parse categories and offers,
- require `vendorCode`,
- normalize `price`,
- preserve all unique images in source order,
- return `FeedSnapshot(..., complete=True)` only after XML parsing reaches the end without exception,
- propagate malformed XML/download-file errors so the caller cannot mistake a partial feed for complete.

- [ ] **Step 7: Run parser/rule tests and verify GREEN**

```bash
PYTHONPATH=liga-kit python -m unittest discover -s liga-kit/tests -v
```

Expected: all Task 2 tests pass.

- [ ] **Step 8: Commit**

```bash
git add liga-kit/liga_kit liga-kit/tests
git commit -m "feat: parse and normalize Liga Divanov feed"
```

---

### Task 3: Add safe HTTP transport and an isolated KIT client

**Files:**
- Create: `liga-kit/liga_kit/http.py`
- Create: `liga-kit/liga_kit/kit_client.py`
- Create: `liga-kit/tests/test_http.py`
- Create: `liga-kit/tests/test_kit_client.py`

**Interfaces:**
- Produces:
  - `SafeSession.request_json(...)`
  - `SafeSession.download_to_file(...)`
  - `KitClient.resolve_warehouse_exact(title)`
  - `KitClient.index_liga_variants()`
  - category, characteristic, product, variant, file upload and bulk price/stock methods
- Consumed by mapper/sync/CLI.
- The client is copied/adapted as infrastructure, but it contains no Samson imports or `SAMS-` ownership logic.

- [ ] **Step 1: Write failing isolation/index tests**

```python
from liga_kit.kit_client import index_liga_variants

rows = [
    {'id': 'l1', 'sku': 'liga-109775'},
    {'id': 's1', 'sku': 'SAMS-109775'},
    {'id': 'n1', 'sku': 'NORMAL-1'},
]
index, duplicates = index_liga_variants(rows)

self.assertEqual(set(index), {'liga-109775'})
self.assertEqual(duplicates, {})
```

Duplicate test:

```python
rows = [
    {'id': 'a', 'sku': 'liga-1'},
    {'id': 'b', 'sku': 'liga-1'},
]
index, duplicates = index_liga_variants(rows)
self.assertNotIn('liga-1', index)
self.assertEqual(len(duplicates['liga-1']), 2)
```

Warehouse test:

```python
self.assertEqual(resolve_exact_warehouse(
    [{'id':'spb','title':'СПБ'}, {'id':'msk','title':'МСК'}],
    'МСК'
), 'msk')
```

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONPATH=liga-kit python -m unittest discover -s liga-kit/tests -v
```

Expected: missing `liga_kit.kit_client` / `liga_kit.http`.

- [ ] **Step 3: Implement safe HTTP transport**

Match the proven retry behavior already used by Samson:
- retry 429/500/502/503/504,
- max 4 attempts,
- bounded exponential delay,
- honor numeric `Retry-After`,
- redact Bearer token from error detail,
- stream file downloads in 128 KiB chunks.

Do not import from `samson_kit.http`; keep Liga deployable/testable independently.

- [ ] **Step 4: Implement Liga KIT client**

Core prefix index:

```python
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
```

`KitClient` must expose:

```python
list_active_warehouses()
resolve_warehouse_exact(title)
iter_variants(filters=None)
index_liga_variants()
list_categories()
create_category(title, parent_id=None)
list_characteristics()
create_characteristic(title, char_type='STRING', select_mode='SINGLE', unit=None)
create_product(category_id)
create_variant(payload)
get_variant(variant_id)
update_variant(variant_id, payload)
upload_image(path)
bulk_update_prices(items)
bulk_update_stocks(items)
```

Use the same current KIT endpoint shapes already verified by the Samson integration.

- [ ] **Step 5: Verify GREEN**

```bash
PYTHONPATH=liga-kit python -m unittest discover -s liga-kit/tests -v
```

Expected: all Task 2–3 tests pass.

- [ ] **Step 6: Commit**

```bash
git add liga-kit/liga_kit/http.py liga-kit/liga_kit/kit_client.py liga-kit/tests
git commit -m "feat: add isolated KIT transport for Liga sync"
```

---

### Task 4: Map categories, characteristics and strict initial images

**Files:**
- Create: `liga-kit/liga_kit/mapper.py`
- Create: `liga-kit/tests/test_mapper.py`
- Create: `liga-kit/tests/test_media.py`

**Interfaces:**
- Produces:
  - `characteristics_from_offer(offer) -> list[tuple[str, list[str]]]`
  - `SyncRunner._ensure_category(...)`
  - `SyncRunner._ensure_characteristics(...)`
  - `SyncRunner._prepare_media(offer) -> list[dict]`
- Consumed by new-product creation in Task 5.

- [ ] **Step 1: Write failing characteristic tests**

Required behavior:

```python
chars = characteristics_from_offer(offer)
self.assertIn(('Цвет', ['Бежевый']), chars)
self.assertIn(('Коллекция', ['Милтон']), chars)
self.assertIn(('Штрихкод', ['109775']), chars)
self.assertIn(('Вес', ['150']), chars)
self.assertIn(('Габариты', ['320/106/88']), chars)
self.assertIn(('Артикул Liga', ['109775']), chars)
```

When the same parameter name has distinct values, preserve deterministic order:

```python
self.assertIn(('Ножки', ['Деревянные', 'Пластиковые']), chars)
```

Do not split a single source value merely because it contains punctuation unless it came from repeated `param` nodes; the feed's text remains source data.

- [ ] **Step 2: Write failing strict media test**

```python
offer.images = ['https://img/1.jpg', 'https://img/2.jpg']

# Fake HTTP succeeds only for first URL.
with self.assertRaises(RuntimeError) as ctx:
    runner._prepare_media(offer)

self.assertIn('prepared 1 of 2', str(ctx.exception))
```

Also test all-success order:

```python
media = runner._prepare_media(offer)
self.assertEqual([m['display_sequence'] for m in media], [0, 1])
self.assertEqual(len(media), 2)
```

- [ ] **Step 3: Run tests and verify RED**

```bash
PYTHONPATH=liga-kit python -m unittest discover -s liga-kit/tests -v
```

Expected: mapper/media functions missing.

- [ ] **Step 4: Implement characteristic mapping**

Use a stable ordered list:
1. all normalized feed params in source encounter order,
2. barcode as `Штрихкод`,
3. country as `Страна производства` when present,
4. warranty as `Гарантия производителя`,
5. weight as `Вес`,
6. dimensions as `Габариты`,
7. source vendorCode as `Артикул Liga`.

Before adding a title/value pair, de-duplicate exact value repeats.

- [ ] **Step 5: Implement category and characteristic resolution**

Category matching key:
- case-folded title,
- expected parent id.

Characteristic matching:
- one value → KIT type `STRING`,
- multiple values → KIT type `MULTIPLE_STRING`,
- more than one existing KIT characteristic with the same title+required type → skip and emit warning,
- never choose a random ID.

Dry-run uses synthetic IDs and performs no write.

- [ ] **Step 6: Implement strict image preparation**

Use:

```python
def _prepare_media(self, offer):
    if self.dry_run:
        return []

    media = []
    for url in offer.images:
        suffix = os.path.splitext(urlparse(url).path)[1] or '.jpg'
        with tempfile.TemporaryDirectory(prefix='liga-img-') as td:
            path = os.path.join(td, 'image' + suffix[:10])
            self.http.download_to_file(url, path)
            uploaded = self.kit.upload_image(path)
            file_id = str(uploaded.get('id', '')).strip()
            if not file_id:
                raise RuntimeError('KIT did not return image file id')
            media.append({
                'type': 'IMAGE',
                'display_sequence': len(media),
                'image_id': file_id,
            })

    if len(media) != len(offer.images):
        raise RuntimeError(
            f'incomplete image set for {offer.kit_sku}: '
            f'prepared {len(media)} of {len(offer.images)}'
        )
    return media
```

Any download/upload exception propagates to the item-level error handler; new product creation must not proceed after an incomplete media set.

- [ ] **Step 7: Verify GREEN and commit**

```bash
PYTHONPATH=liga-kit python -m unittest discover -s liga-kit/tests -v
git add liga-kit/liga_kit liga-kit/tests
git commit -m "feat: map Liga content and require complete images"
```

---

### Task 5: Implement new-product creation and price/stock-only existing updates

**Files:**
- Create: `liga-kit/liga_kit/sync.py`
- Create: `liga-kit/tests/test_sync.py`

**Interfaces:**
- Produces:
  - `build_price_update(offer, variant)`
  - `build_stock_updates(offer, variant, warehouse_ids)`
  - `absent_zero_updates(index, seen_skus, warehouse_ids, complete)`
  - `SyncRunner.run()`
- Existing Liga path never calls content update APIs.
- New Liga path may create category/characteristics/media/product/variant once.

- [ ] **Step 1: Write failing price tests**

KIT effective Liga price must equal feed price. Represent it with equal base and manual-discount values so no stale discount can make the storefront price differ:

```python
update = build_price_update(offer_87990, variant_with_other_price)
self.assertEqual(update, {
    'variant_id': 'v1',
    'price': '87990.00',
    'manual_discount_price': '87990.00',
})
```

Unchanged test:

```python
variant = {
    'id':'v1',
    'pricing': {'price':'87990', 'manual_discount_price':'87990'}
}
self.assertIsNone(build_price_update(offer_87990, variant))
```

Invalid feed price leaves existing price unchanged.

- [ ] **Step 2: Write failing two-warehouse stock tests**

Active:

```python
updates = build_stock_updates(
    active_offer,
    variant_with_spb_0_msk_0,
    {'СПБ':'spb-id', 'МСК':'msk-id'}
)
self.assertEqual(
    {(x['warehouse_id'], x['quantity']) for x in updates},
    {('spb-id', 100), ('msk-id', 100)}
)
```

Unavailable:

```python
self.assertEqual(
    {(x['warehouse_id'], x['quantity']) for x in updates_for_unavailable},
    {('spb-id', 0), ('msk-id', 0)}
)
```

Assert no third warehouse appears.

- [ ] **Step 3: Write failing absent-reconciliation safety tests**

Complete feed:

```python
updates = absent_zero_updates(
    {'liga-1': variant_nonzero},
    seen_skus=set(),
    warehouse_ids={'СПБ':'spb-id','МСК':'msk-id'},
    complete=True,
)
self.assertEqual(len(updates), 2)
self.assertTrue(all(x['quantity'] == 0 for x in updates))
```

Incomplete feed:

```python
self.assertEqual(
    absent_zero_updates(
        {'liga-1': variant_nonzero},
        seen_skus=set(),
        warehouse_ids={'СПБ':'spb-id','МСК':'msk-id'},
        complete=False,
    ),
    []
)
```

- [ ] **Step 4: Write failing existing-content immutability test**

Use a fake KIT client that raises if `update_variant()`, category creation, characteristic creation or image upload is called for an already indexed Liga SKU. Feed price/stock updates are allowed only through bulk price/stock methods.

Expected: `SyncRunner.run()` updates price/stocks without touching content.

- [ ] **Step 5: Run tests and verify RED**

```bash
PYTHONPATH=liga-kit python -m unittest discover -s liga-kit/tests -v
```

Expected: sync functions/classes missing.

- [ ] **Step 6: Implement price/stock comparison helpers**

Use decimal comparison normalized to KIT's whole-ruble behavior for equality checks, while preserving feed price in outgoing payload:

```python
def build_price_update(offer, variant):
    if offer.price is None:
        return None
    desired = f'{offer.price:.2f}'
    pricing = variant.get('pricing') or {}
    if _kit_money(pricing.get('price')) == _kit_money(offer.price) and        _kit_money(pricing.get('manual_discount_price')) == _kit_money(offer.price):
        return None
    variant_id = str(variant.get('id','')).strip()
    return {
        'variant_id': variant_id,
        'price': desired,
        'manual_discount_price': desired,
    } if variant_id else None
```

Stock builder loops exactly over `СПБ` and `МСК` IDs and emits only changed quantities.

- [ ] **Step 7: Implement new product payload**

New variant payload:

```python
payload = {
    'sku': offer.kit_sku,
    'name': offer.name,
    'description': offer.description,
    'status': 'PUBLISHED',
    'product_id': str(product_id),
    'pricing': {
        'price': f'{offer.price:.2f}',
        'manual_discount_price': f'{offer.price:.2f}',
    },
    'stocks': [
        {'warehouse_id': warehouse_ids['СПБ'], 'quantity': desired_stock(offer.available), 'reserved': 0},
        {'warehouse_id': warehouse_ids['МСК'], 'quantity': desired_stock(offer.available), 'reserved': 0},
    ],
}
```

Add `brand=offer.vendor`, `characteristics`, and `media` only when non-empty.

If `offer.price is None`, record item error and skip new creation rather than creating an unpriced storefront product.

- [ ] **Step 8: Implement `SyncRunner.run()` data flow**

Required order:

```text
resolve СПБ + МСК exactly
index only liga-* variants
load KIT categories/characteristics
for each parsed offer:
    seen.add(liga sku)
    if duplicate liga sku in KIT: skip + report
    if existing:
        compare price
        compare СПБ stock
        compare МСК stock
        queue only changed price/stock
    else:
        resolve/create category
        resolve/create characteristics
        prepare ALL images
        create KIT product
        create KIT variant
after complete feed:
    zero missing liga-* on СПБ + МСК
flush price/stock batches
```

If catalog completeness is false, absent zeroing must return no operations.

- [ ] **Step 9: Verify GREEN**

```bash
PYTHONPATH=liga-kit python -m unittest discover -s liga-kit/tests -v
```

Expected: all Task 2–5 tests pass with no failures.

- [ ] **Step 10: Commit**

```bash
git add liga-kit/liga_kit/sync.py liga-kit/tests/test_sync.py
git commit -m "feat: synchronize Liga prices stocks and new products"
```

---

### Task 6: Add configuration, CLI and compact run reporting

**Files:**
- Create: `liga-kit/liga_kit/config.py`
- Create: `liga-kit/liga_kit/report.py`
- Create: `liga-kit/liga_kit/cli.py`
- Create: `liga-kit/tests/test_cli.py`
- Create: `liga-kit/state/.gitkeep`
- Create: `liga-kit/README.md`

**Interfaces:**
- Produces:
  - `Settings.from_env()`
  - CLI options `--dry-run`, `--max-items`, `--report`
  - `liga-kit/state/last_sync.json`
- Consumed by GitHub workflow.

- [ ] **Step 1: Write failing configuration tests**

```python
settings = Settings(kit_token='x')
self.assertEqual(settings.feed_url, 'https://ligadivanov.ru/acrit.export/ym_vendormodel_2023.xml')
self.assertEqual(settings.warehouse_names, ('СПБ', 'МСК'))
```

`Settings.from_env()` must raise when `YANDEX_KIT_TOKEN` is absent.

- [ ] **Step 2: Write failing CLI parser tests**

```python
args = build_parser().parse_args(['--dry-run','--max-items','3'])
self.assertTrue(args.dry_run)
self.assertEqual(args.max_items, 3)
```

- [ ] **Step 3: Run tests and verify RED**

```bash
PYTHONPATH=liga-kit python -m unittest discover -s liga-kit/tests -v
```

- [ ] **Step 4: Implement settings**

```python
from dataclasses import dataclass
import os

@dataclass(frozen=True)
class Settings:
    kit_token: str
    feed_url: str = 'https://ligadivanov.ru/acrit.export/ym_vendormodel_2023.xml'
    warehouse_names: tuple[str, str] = ('СПБ', 'МСК')
    kit_base_url: str = 'https://api.kit.yandex.net'

    @classmethod
    def from_env(cls):
        token = os.environ.get('YANDEX_KIT_TOKEN', '').strip()
        if not token:
            raise RuntimeError('YANDEX_KIT_TOKEN is not configured')
        return cls(kit_token=token)
```

- [ ] **Step 5: Implement CLI download/run lifecycle**

The CLI must:
1. create a temporary directory,
2. download the full feed to a temporary XML file,
3. call `parse_feed()`,
4. apply `--max-items` only after successful full-feed parsing,
5. mark `catalog_complete=False` for a limited smoke run so absent-zero reconciliation cannot fire,
6. instantiate `SyncRunner`,
7. write compact JSON report even on bootstrap failure,
8. return non-zero for full live run when catalog completeness is false.

Parser:

```python
def build_parser():
    p = argparse.ArgumentParser(description='Liga Divanov → Yandex KIT synchronizer')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--max-items', type=int, default=None)
    p.add_argument('--report', default='liga-kit/state/last_sync.json')
    return p
```

- [ ] **Step 6: Report schema**

At minimum:

```python
{
  'status': 'ok',
  'dry_run': False,
  'catalog_complete': True,
  'offers_seen': 4918,
  'active_offers': 0,
  'unavailable_offers': 0,
  'new_products_created': 0,
  'price_changes': 0,
  'spb_stock_changes': 0,
  'msk_stock_changes': 0,
  'unavailable_to_zero': 0,
  'absent_to_zero': 0,
  'duplicate_kit_skus': 0,
  'invalid_price_count': 0,
  'image_failure_count': 0,
  'warning_count': 0,
  'error_count': 0,
}
```

Keep only bounded examples for warnings/errors; never store feed payload or secrets.

- [ ] **Step 7: Verify and commit**

```bash
PYTHONPATH=liga-kit python -m unittest discover -s liga-kit/tests -v
git add liga-kit
git commit -m "feat: add Liga sync CLI reporting and config"
```

---

### Task 7: Add the permanent GitHub Actions workflow

**Files:**
- Create: `.github/workflows/liga-kit-sync.yml`

**Interfaces:**
- Manual dry-run/smoke/full live execution.
- Scheduled Monday 07:00 UTC execution.
- Uses only `YANDEX_KIT_TOKEN`.

- [ ] **Step 1: Add workflow with manual inputs and weekly cron**

```yaml
name: Liga Divanov to Yandex KIT

on:
  workflow_dispatch:
    inputs:
      dry_run:
        description: 'Dry run: do not write to KIT'
        required: true
        default: true
        type: boolean
      max_items:
        description: 'Offer limit for safe validation; 0 means full feed'
        required: true
        default: '20'
        type: string
  schedule:
    - cron: '0 7 * * 1'

permissions:
  contents: read

concurrency:
  group: liga-kit-sync
  cancel-in-progress: false

jobs:
  sync:
    runs-on: ubuntu-latest
    timeout-minutes: 300
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run Liga tests
        run: PYTHONPATH=liga-kit python -m unittest discover -s liga-kit/tests -v
      - name: Run Liga sync
        env:
          YANDEX_KIT_TOKEN: ${{ secrets.YANDEX_KIT_TOKEN }}
        shell: bash
        run: |
          set -euo pipefail
          args=(--report liga-kit/state/last_sync.json)

          if [[ "${{ github.event_name }}" == "workflow_dispatch" ]]; then
            if [[ "${{ inputs.dry_run }}" == "true" ]]; then
              args+=(--dry-run)
            fi
            if [[ "${{ inputs.max_items }}" != "0" ]]; then
              args+=(--max-items "${{ inputs.max_items }}")
            fi
          fi

          PYTHONPATH=liga-kit python -m liga_kit.cli "${args[@]}"
      - name: Print compact report
        if: always()
        run: cat liga-kit/state/last_sync.json || true
```

Scheduled invocation receives no manual inputs, so it always performs a full live sync.

- [ ] **Step 2: Validate workflow syntax and all Python tests before commit**

Run:

```bash
python - <<'PY'
import pathlib
text = pathlib.Path('.github/workflows/liga-kit-sync.yml').read_text()
assert "cron: '0 7 * * 1'" in text
assert 'liga-kit' in text
assert 'YANDEX_KIT_TOKEN' in text
assert 'SAMSON_API_KEY' not in text
print('OK')
PY

PYTHONPATH=liga-kit python -m unittest discover -s liga-kit/tests -v
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/liga-kit-sync.yml
git commit -m "ci: add weekly Liga Divanov KIT sync"
```

---

### Task 8: Controlled verification and first import

**Files:**
- No production-code changes unless a test exposes a defect.
- Temporary one-off workflows are allowed only when needed for diagnosis and must be deleted after use.
- Permanent workflow remains `.github/workflows/liga-kit-sync.yml`.

**Interfaces:**
- Proves the integration against the real feed and real KIT before full rollout.

- [ ] **Step 1: Fresh full automated verification**

Run in GitHub Actions or an equivalent clean runner:

```bash
pip install -r requirements.txt
PYTHONPATH=liga-kit python -m unittest discover -s liga-kit/tests -v
```

Acceptance: zero failures/errors.

- [ ] **Step 2: Full dry-run with no item limit**

Run:

```bash
PYTHONPATH=liga-kit python -m liga_kit.cli   --dry-run   --report liga-kit/state/last_sync.json
```

Acceptance checks:
- feed parses completely,
- `offers_seen` is plausible relative to the current feed,
- no non-`liga-*` SKU appears in planned writes,
- both exact warehouses resolve,
- absent-zero plan is based only on complete feed,
- no secret appears in logs.

- [ ] **Step 3: Live smoke test for exactly three new offers**

Run:

```bash
PYTHONPATH=liga-kit python -m liga_kit.cli   --max-items 3   --report liga-kit/state/last_sync.json
```

Because `max_items` is set, `catalog_complete` must be false and absent reconciliation must be disabled.

For each created card, verify by reading KIT back:
- SKU is `liga-<vendorCode>`,
- effective price equals feed `price`,
- СПБ stock is 100 for active or 0 for unavailable,
- МСК stock is 100 for active or 0 for unavailable,
- no third warehouse changed,
- category/name/description/brand/characteristics are present,
- number of KIT images equals normalized source image count.

- [ ] **Step 4: Verify Webasyst v0.9.7 deployment before full live import**

Run one normal Webasyst sync after installing v0.9.7.

Acceptance:
- no `liga-*` in missing-on-site warnings,
- no `liga-*` price/stock payloads,
- no `liga-*` image/KIT-ID/create processing,
- non-external Webasyst sync remains functional.

- [ ] **Step 5: First full live import**

Run the permanent workflow manually with:

```text
dry_run = false
max_items = 0
```

Acceptance:
- run reaches complete-feed condition,
- new `liga-*` products are created without duplicate SKU,
- item errors are bounded/reported,
- no `SAMS-*` or ordinary SKU is mutated,
- absent/unavailable Liga items are the only candidates for 0 stock.

- [ ] **Step 6: Post-import sampling**

Check at least:
- one product with 10+ images,
- one product with repeated params,
- one active product,
- one unavailable product if present,
- one product in each of several different source categories.

Compare source feed to KIT for SKU, price, both stocks, image count and characteristics.

- [ ] **Step 7: Final permanent-workflow check**

Confirm `.github/workflows/liga-kit-sync.yml` still contains:

```yaml
schedule:
  - cron: '0 7 * * 1'
```

and there are no temporary Liga probe/smoke/live workflows left enabled.

- [ ] **Step 8: Final commit only if verification required code fixes**

For each defect found during rollout:
1. add a failing regression test reproducing the real defect,
2. verify RED,
3. make the minimal production fix,
4. rerun the full Liga test suite,
5. commit that fix separately.

---

## Final Requirements Checklist

Before declaring the Liga integration complete, verify each requirement explicitly:

- `109775` maps to `liga-109775`.
- Feed price is transferred with no markup.
- Active product has 100 on СПБ and 100 on МСК.
- Unavailable product has 0 on СПБ and 0 on МСК.
- Confirmed absent product has 0 on СПБ and 0 on МСК only after complete feed retrieval.
- Broken/partial feed cannot mass-zero Liga products.
- New product receives all normalized source images or remains retryable.
- Existing Liga product gets only price/stock updates.
- Existing Liga name/description/images/characteristics/category are unchanged by weekly sync.
- Webasyst ignores both `SAMS-*` and `liga-*`.
- Samson never modifies `liga-*`.
- Liga never modifies `SAMS-*` or ordinary SKUs.
- Weekly cron is Monday 07:00 UTC / 10:00 Moscow.
- No secrets, XML catalog snapshots or image archives are committed.
