# Samson → Yandex KIT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lightweight, resumable GitHub Actions integration in `gera5malyov-jpg/norden-yml` that imports the full Samson catalog into Yandex KIT as `SAMS-*`, then every Monday at 10:00 Moscow time updates existing Samson prices/stocks and creates newly appeared products.

**Architecture:** A Python package under `samson-kit/` reads the official paginated Samson `/v1/sku/` and `/v1/category/` APIs using the API key only from environment variables, builds a KIT index, and applies idempotent changes through the same KIT REST endpoints already proven by the Webasyst plugin. Full source payloads and images are never committed; only compact state/report JSON is kept. Existing products get only price and `СПБ` stock updates, while new products are created with maximum supported Samson content. A complete-catalog flag gates removed-item zeroing.

**Tech Stack:** Python 3.12, standard library (`unittest`, `decimal`, `tempfile`, `json`, `urllib.parse`) plus existing `requests`; GitHub Actions `ubuntu-latest`; Samson API v1; Yandex KIT REST API.

**Spec:** `docs/superpowers/specs/2026-09-17-samson-kit-sync-design.md`

## Global Constraints

- Source documentation: `https://api.samsonopt.ru/v1/doc/index.html`; source overview confirms paginated full assortment and stock/price fields: `https://info.samsonopt.ru/api`.
- Samson authentication is `api_key` query parameter; never log the key or a URL containing it.
- KIT authentication is `Authorization: Bearer <token>`; never log the header/token.
- GitHub Secrets are exactly `SAMSON_API_KEY` and `YANDEX_KIT_TOKEN`.
- Target KIT warehouse is the unique active warehouse with exact title `СПБ`; any zero/multiple match stops stock writes.
- SKU mapping is deterministic: Samson code/article `531863` → KIT SKU `SAMS-531863`.
- Existing `SAMS-*` products: weekly update only price and `СПБ` stock. Do not rewrite content.
- New `SAMS-*` products: create category hierarchy and transfer maximum compatible Samson information, including barcodes, characteristics/facets and images when available.
- Active confirmed total Samson stock `> 0` → that total; active confirmed total `= 0` → `100`; withdrawn/deleted → `0`.
- “Absent from Samson” may become `0` only after a provably complete full catalog traversal.
- Purchase price `<= 3000.00` → sale `×1.40`; purchase price `>3000.00` → sale `×1.26`; before-discount `×1.80`; minimum `×1.20` is calculated and reported but is not sent unless a verified KIT field exists. Current proven KIT integration only uses `price` and `manual_discount_price`.
- No full catalog snapshot, photo archive or large GitHub artifact is stored.
- Scheduled workflow uses one standard `ubuntu-latest` job, one concurrency group, and no paid/larger runner.
- Webasyst `SAMS-*` exclusion v0.9.6 must be deployed and verified before scheduled live import is enabled.

---

## File map

Create:

```text
samson-kit/
  README.md
  samson_kit/
    __init__.py
    config.py          # env/config validation and constants
    http.py            # redacted/retrying requests.Session wrapper
    rules.py           # SKU, Decimal price and stock business rules
    samson_client.py   # paginated Samson category/SKU client
    kit_client.py      # KIT warehouses/variants/categories/chars/files/create/bulk update
    mapper.py          # Samson record → normalized/new KIT card payload pieces
    sync.py            # reconciliation/orchestration and completeness guard
    report.py          # compact report/checkpoint writer
    cli.py             # dry-run/live entry point
  tests/
    test_rules.py
    test_samson_client.py
    test_mapper.py
    test_sync.py
  state/
    last_sync.json     # generated compact summary; no secrets/full payload
.github/workflows/samson-kit-sync.yml
```

Modify only if needed:

```text
requirements.txt       # keep `requests`; do not add heavy frameworks
.gitignore             # ignore temporary downloaded media/cache if implementation creates local temp dirs
```

---

### Task 1: Build configuration and pure business rules with tests

**Files:**
- Create: `samson-kit/samson_kit/__init__.py`
- Create: `samson-kit/samson_kit/config.py`
- Create: `samson-kit/samson_kit/rules.py`
- Create: `samson-kit/tests/test_rules.py`

**Interfaces:**
- Produces: `Settings.from_env()`, `to_kit_sku()`, `PriceDecision`, `calculate_prices()`, `calculate_stock()`.
- Later tasks must use these functions instead of duplicating rules.

- [ ] **Step 1: Write failing rule tests**

```python
# samson-kit/tests/test_rules.py
import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from samson_kit.rules import calculate_prices, calculate_stock, to_kit_sku


class RuleTests(unittest.TestCase):
    def test_sku_prefix(self):
        self.assertEqual(to_kit_sku("531863"), "SAMS-531863")

    def test_price_at_threshold_uses_140(self):
        p = calculate_prices(Decimal("3000.00"))
        self.assertEqual(p.sale, Decimal("4200.00"))
        self.assertEqual(p.old, Decimal("5400.00"))
        self.assertEqual(p.minimum, Decimal("3600.00"))

    def test_price_above_threshold_uses_126(self):
        p = calculate_prices(Decimal("3000.01"))
        self.assertEqual(p.sale, Decimal("3780.01"))

    def test_active_positive_stock_is_sum(self):
        self.assertEqual(calculate_stock([2, 3, 4], active=True, withdrawn=False), 9)

    def test_active_confirmed_zero_becomes_100(self):
        self.assertEqual(calculate_stock([0, 0, 0], active=True, withdrawn=False), 100)

    def test_withdrawn_is_zero(self):
        self.assertEqual(calculate_stock([5, 7], active=False, withdrawn=True), 0)

    def test_missing_stock_returns_none(self):
        self.assertIsNone(calculate_stock(None, active=True, withdrawn=False))

    def test_negative_stock_is_unsafe(self):
        self.assertIsNone(calculate_stock([2, -1], active=True, withdrawn=False))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

```bash
PYTHONPATH=samson-kit python -m unittest samson-kit/tests/test_rules.py -v
```

Expected: import/module failure because implementation does not exist.

- [ ] **Step 3: Implement config and rules**

`config.py` must define exact constants and strict env validation:

```python
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    samson_api_key: str
    kit_token: str
    target_warehouse: str = "СПБ"
    samson_base_url: str = "https://api.samsonopt.ru/v1"
    kit_base_url: str = "https://api.kit.yandex.net"

    @classmethod
    def from_env(cls):
        samson = os.environ.get("SAMSON_API_KEY", "").strip()
        kit = os.environ.get("YANDEX_KIT_TOKEN", "").strip()
        if not samson:
            raise RuntimeError("SAMSON_API_KEY is not configured")
        if not kit:
            raise RuntimeError("YANDEX_KIT_TOKEN is not configured")
        return cls(samson_api_key=samson, kit_token=kit)
```

`rules.py` must use `Decimal` with `ROUND_HALF_UP`:

```python
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

MONEY = Decimal("0.01")


@dataclass(frozen=True)
class PriceDecision:
    sale: Decimal
    old: Decimal
    minimum: Decimal


def to_kit_sku(samson_code):
    code = str(samson_code).strip()
    if not code:
        raise ValueError("empty Samson code")
    return "SAMS-" + code


def _money(value):
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def calculate_prices(purchase):
    purchase = Decimal(purchase)
    if purchase <= 0:
        return None
    sale_factor = Decimal("1.40") if purchase <= Decimal("3000.00") else Decimal("1.26")
    return PriceDecision(
        sale=_money(purchase * sale_factor),
        old=_money(purchase * Decimal("1.80")),
        minimum=_money(purchase * Decimal("1.20")),
    )


def calculate_stock(parts, *, active, withdrawn):
    if withdrawn:
        return 0
    if not active or parts is None:
        return None
    values = [int(v) for v in parts]
    if any(v < 0 for v in values):
        return None
    total = sum(values)
    return total if total > 0 else 100
```

- [ ] **Step 4: Run the tests**

```bash
PYTHONPATH=samson-kit python -m unittest samson-kit/tests/test_rules.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add samson-kit/samson_kit samson-kit/tests/test_rules.py
git commit -m "feat: add Samson KIT business rules"
```

---

### Task 2: Add a redacting HTTP client and paginated Samson API client

**Files:**
- Create: `samson-kit/samson_kit/http.py`
- Create: `samson-kit/samson_kit/samson_client.py`
- Create: `samson-kit/tests/test_samson_client.py`

**Interfaces:**
- Produces: `SafeSession.request_json()`, `SamsonClient.iter_categories()`, `SamsonClient.iter_skus()`.
- `iter_skus()` yields normalized raw dict records page-by-page and returns/records whether traversal ended normally.
- Uses official endpoints `GET /category/` and `GET /sku/`, `response_format=json`, `pagination_page=N`, plus `api_key` injected inside the client.

- [ ] **Step 1: Write parser/pagination tests using fake responses**

```python
# samson-kit/tests/test_samson_client.py
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from samson_kit.samson_client import parse_page


class SamsonPageTests(unittest.TestCase):
    def test_parses_documented_envelope(self):
        payload = [{
            "data": [{"sku": "531863"}],
            "meta": {"pagination": {"next": "https://api.samsonopt.ru/v1/sku/?pagination_page=2&api_key=SECRET"}},
        }]
        data, next_page = parse_page(payload)
        self.assertEqual(data[0]["sku"], "531863")
        self.assertEqual(next_page, 2)

    def test_last_page_has_no_next(self):
        data, next_page = parse_page({"data": [{"sku": "1"}], "meta": {"pagination": {"next": None}}})
        self.assertEqual(len(data), 1)
        self.assertIsNone(next_page)

    def test_rejects_invalid_payload(self):
        with self.assertRaises(ValueError):
            parse_page({"unexpected": []})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run and verify failure**

```bash
PYTHONPATH=samson-kit python -m unittest samson-kit/tests/test_samson_client.py -v
```

Expected: import failure.

- [ ] **Step 3: Implement `SafeSession` with bounded retry and secret-safe errors**

Use one `requests.Session`, connect timeout 10s, read timeout 60s, max 4 attempts for 429/500/502/503/504 and transport errors. Sleep sequence: 1, 2, 4 seconds, honoring numeric `Retry-After` when it is larger but capping a single wait at 30 seconds. Error text may include method, host, path and status but never query parameters or Authorization headers.

Public method:

```python
def request_json(self, method, url, *, params=None, headers=None, json_body=None, files=None):
    ...
```

- [ ] **Step 4: Implement Samson page parsing without preserving secret-bearing `next` URLs**

```python
from urllib.parse import parse_qs, urlparse


def parse_page(payload):
    envelope = payload[0] if isinstance(payload, list) and len(payload) == 1 else payload
    if not isinstance(envelope, dict) or not isinstance(envelope.get("data"), list):
        raise ValueError("invalid Samson page envelope")
    pagination = (envelope.get("meta") or {}).get("pagination") or {}
    next_url = pagination.get("next")
    if not next_url:
        return envelope["data"], None
    query = parse_qs(urlparse(next_url).query)
    values = query.get("pagination_page") or []
    if not values or not str(values[0]).isdigit():
        raise ValueError("invalid Samson next-page cursor")
    return envelope["data"], int(values[0])
```

Do not store or log `next_url`; extract only the integer page.

- [ ] **Step 5: Implement `SamsonClient` pagination**

Each request calls:

```python
self.http.request_json(
    "GET",
    self.base_url + "/sku/",
    params={
        "api_key": self.api_key,
        "response_format": "json",
        "pagination_page": page,
    },
    headers={"Accept": "application/json"},
)
```

Categories use the same pattern with `/category/`.

Reject pagination loops by keeping only page integers in a `seen_pages` set. A failure before natural `next=None` must propagate so the orchestrator marks the catalog incomplete and disables absent-item zeroing.

- [ ] **Step 6: Run Samson client tests**

```bash
PYTHONPATH=samson-kit python -m unittest samson-kit/tests/test_samson_client.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add samson-kit/samson_kit/http.py samson-kit/samson_kit/samson_client.py samson-kit/tests/test_samson_client.py
git commit -m "feat: add paginated Samson API client"
```

---

### Task 3: Add the Yandex KIT client using the already-proven REST contract

**Files:**
- Create: `samson-kit/samson_kit/kit_client.py`
- Extend: `samson-kit/tests/test_sync.py`

**Interfaces:**
- Produces:
  - `list_active_warehouses()`
  - `resolve_warehouse_exact("СПБ")`
  - `iter_variants()` and `index_samson_variants()`
  - `list_categories()`, `create_category()`
  - `list_characteristics()`, `create_characteristic()`
  - `upload_image()`
  - `create_product()`, `create_variant()`
  - `bulk_update_prices(items)`
  - `bulk_update_stocks(items)`
- All calls use `Authorization: Bearer` and `https://api.kit.yandex.net`.

- [ ] **Step 1: Write a warehouse-resolution unit test**

```python
import unittest
from samson_kit.kit_client import resolve_exact_warehouse


class WarehouseTests(unittest.TestCase):
    def test_unique_spb(self):
        rows = [{"id": "a", "title": "МСК"}, {"id": "b", "title": "СПБ"}]
        self.assertEqual(resolve_exact_warehouse(rows, "СПБ"), "b")

    def test_duplicate_spb_is_error(self):
        rows = [{"id": "a", "title": "СПБ"}, {"id": "b", "title": "СПБ"}]
        with self.assertRaises(RuntimeError):
            resolve_exact_warehouse(rows, "СПБ")
```

- [ ] **Step 2: Implement collection pagination and endpoints**

Use the proven KIT endpoints:

```text
GET  /v1/warehouses?status=ACTIVE&page=N&per_page=100
GET  /v1/variants?page=N&per_page=100
GET  /v1/categories?status=ACTIVE&page=N&per_page=100
POST /v1/categories
GET  /v1/characteristics?status=ACTIVE&page=N&per_page=100
POST /v1/characteristics
POST /v1/files                      (multipart file)
POST /v1/products
POST /v1/variants
POST /v1/variants/prices/bulk_update
POST /v1/variants/stocks/bulk_update
```

Collection parser must accept direct lists and named-list envelopes (`items`, `results`, `data`, `variants`, `warehouses`, `categories`, `characteristics`) and total keys `total_count`, `total`, `count`.

`index_samson_variants()` must read the KIT catalog once, filter only exact `sku.startswith("SAMS-")`, and return both:

```python
{
    "SAMS-531863": [variant_dict],
    ...
}
```

A list length of more than 1 marks a duplicate; never choose one destructively.

- [ ] **Step 3: Implement bulk write chunking**

Chunk price and stock payloads at 5000 items, matching the working Webasyst implementation:

```python
for chunk in chunks(items, 5000):
    self._json("POST", "/v1/variants/prices/bulk_update", {"items": chunk})
```

and analogously for stocks.

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=samson-kit python -m unittest samson-kit/tests -v
```

Expected: all current tests pass.

- [ ] **Step 5: Commit**

```bash
git add samson-kit/samson_kit/kit_client.py samson-kit/tests/test_sync.py
git commit -m "feat: add Yandex KIT API client"
```

---

### Task 4: Normalize Samson fields and build new-product KIT payloads

**Files:**
- Create: `samson-kit/samson_kit/mapper.py`
- Create: `samson-kit/tests/test_mapper.py`

**Interfaces:**
- Produces `normalize_samson_record(raw) -> SamsonItem` and mapping helpers for categories, characteristics, barcodes, images, packaging and withdrawal state.
- New item payload follows the proven KIT variant shape: `sku`, `name`, `description`, `status`, `product_id`, optional `brand`, `characteristics`, `stocks`, `media`, `pricing`, `cargo_boxes`.

- [ ] **Step 1: Write representative normalization tests**

Use keys documented/observed for Samson integrations: `sku`, `vendor_code`, `manufacturer`, `brand`, `name`, `description`, extended description, `category_list`, `barcode`, `weight`, `volume`, `facet_list`, `photo_list`, personal price, stock fields, withdrawal flag/date.

```python
raw = {
    "sku": "531863",
    "vendor_code": "ABC-1",
    "name": "Тестовый товар",
    "brand": "BRAUBERG",
    "description": "Краткое",
    "description_ext": "Расширенное",
    "barcode": "4600000000001",
    "price": "2500.00",
    "stock": "2",
    "stock_rc": "3",
    "stock_transit": "4",
    "category_list": [{"id": "10", "name": "Офис"}, {"id": "20", "name": "Мебель"}],
    "facet_list": [{"name": "Цвет", "value": "Черный"}],
    "photo_list": ["https://example.test/1.jpg"],
    "out_of_assortment": "0",
}
```

Assert normalized SKU is `SAMS-531863`, purchase `2500.00`, stock parts `[2,3,4]`, hierarchy order is preserved, barcode/image/facet are preserved, and description combines non-empty short/extended text without duplication.

- [ ] **Step 2: Implement tolerant field aliases without inventing values**

`normalize_samson_record()` must prefer documented keys but accept aliases actually encountered in the live API response. Store only values present in the source. Do not fabricate country, dimensions, packaging or manufacturer.

For withdrawal, normalize recognized truthy values (`1`, `true`, `yes`, `да`) from the documented “вывод из ассортимента” field; a documented deletion date at or before the run date also makes the item withdrawn. Unrecognized/malformed status is unsafe and must not be treated as active-zero.

For stock, collect every documented location quantity available in the SKU record (ИДП, РЦ, in-transit and any additional warehouse/location stock fields returned by the current API contract) into a numeric list. If the API explicitly supplies a stock collection/list, use every element in that collection instead of assuming only three locations.

- [ ] **Step 3: Implement category-chain and characteristic mapping helpers**

Categories are matched in KIT by normalized lower-case title **within the expected parent id**. More than one match in the same parent is an error for that new product; zero matches creates the category.

Facet/characteristic values become KIT characteristics. Reuse an existing characteristic only when normalized title and compatible type match; otherwise create a `TEXT` characteristic with `select_mode` compatible with a free-text value. Keep mapping in memory for the run; do not persist the whole Samson catalog.

- [ ] **Step 4: Implement image handling contract**

For a new item only:

1. Stream-download each unique Samson image URL into `tempfile.TemporaryDirectory()`.
2. Enforce HTTP success and a finite per-file size ceiling of 25 MiB to prevent runaway downloads.
3. Upload it to KIT `/v1/files`.
4. Add returned file id to variant media as:

```python
{"type": "IMAGE", "display_sequence": sequence, "image_id": file_id}
```

5. Delete temp files automatically at context exit.

An optional image failure is reported and skipped; it must not discard an otherwise valid new product.

- [ ] **Step 5: Run mapper tests**

```bash
PYTHONPATH=samson-kit python -m unittest samson-kit/tests/test_mapper.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add samson-kit/samson_kit/mapper.py samson-kit/tests/test_mapper.py
git commit -m "feat: map Samson catalog data to KIT"
```

---

### Task 5: Implement idempotent sync orchestration and mass-zero protection

**Files:**
- Create: `samson-kit/samson_kit/sync.py`
- Complete: `samson-kit/tests/test_sync.py`

**Interfaces:**
- Produces `run_sync(settings, *, dry_run=False, create_limit=None, runtime_budget_seconds=15000) -> SyncReport`.
- Uses dependency-injectable Samson/KIT clients so tests use fakes and never hit live APIs.

- [ ] **Step 1: Write fake-client tests for all destructive rules**

Tests must cover these exact cases:

```text
existing SAMS active stock 9 -> one SPB stock update to 9
existing SAMS active confirmed stock 0 -> SPB stock update to 100
existing SAMS withdrawn -> SPB stock update to 0
existing SAMS price <=3000 -> calculated price update
existing SAMS content changed -> no name/description/image/category write
new SAMS -> create category/product/variant once
same source rerun -> no duplicate create
non-SAMS KIT variant -> untouched
one duplicate SAMS SKU in KIT -> skip and report duplicate
incomplete Samson page traversal -> no absent-item zeroing
complete traversal with old KIT SAMS absent -> zero that variant on SPB
stock parse/error for one item -> leave its KIT stock unchanged
other KIT warehouse quantities -> never included in update payload
```

- [ ] **Step 2: Implement preflight before any write**

Order is mandatory:

```text
1. validate secrets/config
2. resolve unique active KIT warehouse exactly “СПБ”
3. index all KIT SAMS-* variants and duplicates
4. fetch/cache KIT categories and characteristics metadata
5. only then begin Samson traversal and writes
```

If steps 1–3 fail, no writes occur.

- [ ] **Step 3: Stream the full Samson SKU catalog and track completeness**

Maintain only compact structures:

```python
seen_skus: set[str]
price_updates: list[dict]
stock_updates: list[dict]
existing_index: dict[str, list[dict]]
```

Process each Samson page immediately; do not accumulate full raw catalog.

A traversal is `catalog_complete=True` only when pagination reaches a valid natural end (`next=None`) without request, JSON, validation or pagination-loop error. If traversal fails, keep already confirmed per-item safe updates but set `catalog_complete=False` and skip absent reconciliation.

- [ ] **Step 4: Existing-item behavior**

For exactly one existing KIT variant with the source SKU:

```text
compare current KIT price/manual_discount_price against calculated sale/old
compare current KIT СПБ quantity against desired stock
append only changed price/stock entries
never send product/content/category/media/characteristic updates
```

Flush bulk update buffers periodically (for example every 1000 items) to bound memory; final flush at end. In dry-run, record counters but do not call write methods.

- [ ] **Step 5: New-item behavior**

For source SKU absent from KIT:

```text
ensure/reuse category chain
ensure/reuse characteristics
create one KIT product with leaf category id
build one variant with SAMS-* SKU and maximum supported source data
set only СПБ stock in the variant stock block
apply calculated price when purchase price is valid
upload available images temporarily and attach media
```

Re-check exact SKU immediately before create to avoid retry/race duplicates. If it appeared, switch to existing-item behavior instead of creating another.

`create_limit` applies only to **new live creations** during manual smoke tests; it does not truncate the catalog scan or compromise completeness logic. When the limit is reached, continue scanning existing source SKUs without creating further new cards.

- [ ] **Step 6: Removed/absent reconciliation**

Only if `catalog_complete is True`, compute:

```python
absent = set(existing_index) - seen_skus
```

For each non-duplicate absent `SAMS-*` variant, append exactly one stock update:

```python
{"variant_id": variant_id, "warehouse_id": spb_id, "quantity": 0}
```

Do not archive/delete the KIT product and do not touch prices/content.

- [ ] **Step 7: Add runtime-budget safety for the first huge import**

The standard runner job must stay well below GitHub's per-job maximum. Use `runtime_budget_seconds=15000` (4h10m) for live runs. After the budget is exceeded, stop **new card creation and image upload**, continue only long enough to flush already-built price/stock buffers and write a report, and mark `new_creation_budget_exhausted=true`.

Do **not** treat uncreated source SKUs as removed; they are in `seen_skus`. A later manual/full run sees them still missing in KIT and continues creating them. This makes the initial 40k+ catalog import resumable without storing a huge pending file.

- [ ] **Step 8: Run sync tests**

```bash
PYTHONPATH=samson-kit python -m unittest samson-kit/tests/test_sync.py -v
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add samson-kit/samson_kit/sync.py samson-kit/tests/test_sync.py
git commit -m "feat: add safe Samson KIT reconciliation"
```

---

### Task 6: Add compact reporting, CLI, dry-run and contract probe

**Files:**
- Create: `samson-kit/samson_kit/report.py`
- Create: `samson-kit/samson_kit/cli.py`
- Create: `samson-kit/README.md`
- Create initial: `samson-kit/state/last_sync.json`

**Interfaces:**
- CLI examples:
  - `python -m samson_kit.cli --dry-run --create-limit 20`
  - `python -m samson_kit.cli --live`
- Report path: `samson-kit/state/last_sync.json`.

- [ ] **Step 1: Implement a secret-free `SyncReport` dataclass**

Fields must include:

```text
started_at
finished_at
dry_run
catalog_complete
samson_products_seen
new_products_created
new_products_skipped_by_limit
new_creation_budget_exhausted
price_updates
stock_updates
active_zero_set_to_100
withdrawn_set_to_0
absent_set_to_0
duplicate_kit_skus
invalid_price_items
stock_anomalies
mapping_warnings
api_retries
errors
status
```

`errors` contains concise error class/message and SKU when applicable, never request URLs with query strings, tokens, headers or raw full source payloads.

- [ ] **Step 2: Implement CLI flags with safe defaults**

```text
--dry-run              no KIT writes; default unless --live is supplied
--live                 allow writes
--create-limit N       manual smoke-test limit for new cards only
--runtime-budget N     defaults to 15000 seconds
```

Reject specifying both `--dry-run` and `--live`.

- [ ] **Step 3: Add a read-only contract probe before first live run**

`--dry-run --create-limit 20` must perform real authenticated reads from:

```text
GET Samson /category/
GET Samson /sku/
GET KIT /v1/warehouses
GET KIT /v1/variants
GET KIT /v1/categories
GET KIT /v1/characteristics
```

It must report discovered Samson top-level field names as a **sorted list of keys only** for diagnostic mapping, not values/full records. This provides a safe way to reconcile any current API aliases with the mapper without leaking catalog payload or secrets.

- [ ] **Step 4: Write report atomically**

Write to a temp file beside `last_sync.json`, then `os.replace()` so an interrupted process never leaves partial JSON. Initialize repository state file to:

```json
{
  "status": "never_run"
}
```

- [ ] **Step 5: Document manual secret setup and rollout in README**

README must instruct the user to add repository Actions secrets:

```text
SAMSON_API_KEY
YANDEX_KIT_TOKEN
```

Do not put their values in docs/examples.

- [ ] **Step 6: Run full Python tests**

```bash
PYTHONPATH=samson-kit python -m unittest discover -s samson-kit/tests -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add samson-kit/README.md samson-kit/samson_kit/report.py samson-kit/samson_kit/cli.py samson-kit/state/last_sync.json
git commit -m "feat: add Samson sync CLI and reporting"
```

---

### Task 7: Add the low-cost GitHub Actions workflow

**Files:**
- Create: `.github/workflows/samson-kit-sync.yml`

**Interfaces:**
- Scheduled: Monday 07:00 UTC = 10:00 Moscow.
- Manual: dry-run/live and create limit.
- Secrets passed only as process environment variables.

- [ ] **Step 1: Create workflow with one standard job and concurrency**

Use this structure:

```yaml
name: Samson to Yandex KIT

on:
  schedule:
    - cron: '0 7 * * 1'
  workflow_dispatch:
    inputs:
      live:
        description: 'Allow KIT writes'
        required: true
        default: false
        type: boolean
      create_limit:
        description: 'Limit only new product creations; 0 means unlimited'
        required: true
        default: '20'
        type: string

permissions:
  contents: write

concurrency:
  group: samson-kit-sync
  cancel-in-progress: false

jobs:
  sync:
    runs-on: ubuntu-latest
    timeout-minutes: 285
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'
      - run: pip install -r requirements.txt
      - name: Test Samson integration
        run: PYTHONPATH=samson-kit python -m unittest discover -s samson-kit/tests -v
      - name: Run scheduled live sync
        if: github.event_name == 'schedule'
        env:
          SAMSON_API_KEY: ${{ secrets.SAMSON_API_KEY }}
          YANDEX_KIT_TOKEN: ${{ secrets.YANDEX_KIT_TOKEN }}
        run: PYTHONPATH=samson-kit python -m samson_kit.cli --live --runtime-budget 15000
      - name: Run manual sync
        if: github.event_name == 'workflow_dispatch'
        env:
          SAMSON_API_KEY: ${{ secrets.SAMSON_API_KEY }}
          YANDEX_KIT_TOKEN: ${{ secrets.YANDEX_KIT_TOKEN }}
        shell: bash
        run: |
          LIMIT='${{ inputs.create_limit }}'
          ARGS=(--runtime-budget 15000)
          if [ '${{ inputs.live }}' = 'true' ]; then ARGS+=(--live); else ARGS+=(--dry-run); fi
          if [ "$LIMIT" != '0' ]; then ARGS+=(--create-limit "$LIMIT"); fi
          PYTHONPATH=samson-kit python -m samson_kit.cli "${ARGS[@]}"
      - name: Commit compact sync report
        if: always()
        run: |
          if [ ! -f samson-kit/state/last_sync.json ]; then exit 0; fi
          git config user.name github-actions
          git config user.email actions@github.com
          git add samson-kit/state/last_sync.json
          if git diff --cached --quiet; then exit 0; fi
          git commit -m "Update Samson KIT sync status"
          git pull --rebase origin main
          git push
```

- [ ] **Step 2: Verify workflow does not persist large files/artifacts**

Check that the workflow contains no `actions/upload-artifact`, no catalog dump command and no image-directory commit.

- [ ] **Step 3: Run local tests once more**

```bash
PYTHONPATH=samson-kit python -m unittest discover -s samson-kit/tests -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/samson-kit-sync.yml
git commit -m "ci: add weekly Samson KIT sync"
```

---

### Task 8: Verification before enabling live schedule

**Files:**
- No new source files unless contract probe reveals a real documented field alias that must be added to `mapper.py` with a test.

**Interfaces:**
- Produces verified live integration and first-import procedure.

- [ ] **Step 1: Confirm Webasyst prerequisite**

Verify v0.9.6 is installed and one Webasyst sync produced no `SAMS-*` writes/warnings.

- [ ] **Step 2: User adds GitHub Actions secrets manually**

Repository → Settings → Secrets and variables → Actions → New repository secret:

```text
SAMSON_API_KEY
YANDEX_KIT_TOKEN
```

This connector cannot write GitHub secret values; do not put them in files or commit history.

- [ ] **Step 3: Run manual dry-run with 20-create planning limit**

Workflow dispatch:

```text
live = false
create_limit = 20
```

Acceptance criteria from `last_sync.json` and logs:

```text
catalog read succeeds
unique warehouse СПБ resolves
no KIT writes occur
sample source field keys are visible without values/secrets
SAMS-* mapping is correct
price/stock calculations match rules
catalog_complete=true for a complete traversal
```

- [ ] **Step 4: If live Samson field aliases differ, add only tested aliases**

For every alias change: add one fixture-based mapper test first, run it failing, implement the alias, then rerun the full suite. Do not weaken completeness/stock validation to “make data pass”.

- [ ] **Step 5: Run a small live smoke test**

Workflow dispatch:

```text
live = true
create_limit = 20
```

Inspect at least several created KIT cards. Verify:

```text
SKU begins SAMS-
category hierarchy is correct
name/description/brand/barcode/characteristics/images appear when source supplied them
prices use the correct threshold formula
only warehouse СПБ is set
active confirmed-zero stock is 100
no existing non-SAMS variant changed
```

- [ ] **Step 6: Run first full live import repeatedly until no creation budget remains**

Dispatch with:

```text
live = true
create_limit = 0
```

If `new_creation_budget_exhausted=true`, dispatch again. Because matching is by exact deterministic `SAMS-*` SKU, completed cards are skipped and the next run continues with remaining source products without a large persistent queue.

Stop only when:

```text
new_creation_budget_exhausted=false
new_products_created may be 0 on the final confirmation run
catalog_complete=true
errors contain no systemic authentication/warehouse/completeness failure
```

- [ ] **Step 7: Confirm weekly schedule remains enabled**

The workflow schedule is already `0 7 * * 1`; after successful bootstrap, no extra daily job is required. Weekly runs then update existing prices/stocks and create only new Samson items.

- [ ] **Step 8: Final secret scan**

```bash
git grep -nE 'yakit_[A-Za-z0-9_-]+|api_key[[:space:]]*=[[:space:]]*[A-Za-z0-9]{20,}' -- . ':!docs/superpowers/*' && exit 1 || true
```

Also inspect GitHub Actions logs for accidental full query URLs. Expected: no Samson key and no KIT token present.
