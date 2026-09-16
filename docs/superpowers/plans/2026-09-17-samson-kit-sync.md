# Samson → Yandex KIT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a lightweight, resumable Samson API → Yandex KIT integration in `gera5malyov-jpg/norden-yml` that owns only `SAMS-*` SKUs, imports new products fully, and updates existing Samson prices/stocks weekly.

**Architecture:** Python 3.12 code under `samson-kit/` streams the official paginated Samson catalog, indexes existing KIT `SAMS-*` variants, applies deterministic pricing/stock rules, creates only missing products, and performs absent-item zeroing only after a provably complete Samson traversal. Full catalog payloads and images are never committed; only compact sync state is stored. The GitHub workflow is manual-only until the Webasyst `SAMS-*` exclusion plugin is deployed and smoke-tested; only then is the Monday cron enabled.

**Tech Stack:** Python 3.12, standard library + existing `requests`, `unittest`, GitHub Actions `ubuntu-latest`, Samson API v1, Yandex KIT REST API.

**Spec:** `docs/superpowers/specs/2026-09-17-samson-kit-sync-design.md`

## Global Constraints

- Samson docs: `https://api.samsonopt.ru/v1/doc/index.html`.
- Samson full catalog/categories are read from `GET /v1/sku/` and `GET /v1/category/` with `api_key`, `response_format=json`, `pagination_page=N`.
- KIT base URL: `https://api.kit.yandex.net` with `Authorization: Bearer ...`.
- GitHub Secrets: `SAMSON_API_KEY`, `YANDEX_KIT_TOKEN`; values never enter git, logs, reports, URLs shown in exceptions, or committed files.
- KIT warehouse: exact active title `СПБ`; zero or multiple matches stop stock writes.
- SKU: Samson `531863` → KIT `SAMS-531863`.
- Existing `SAMS-*`: weekly change only price and `СПБ` stock.
- New `SAMS-*`: transfer maximum compatible content available from Samson.
- Active confirmed stock sum `>0` → sum; active confirmed sum `0` → `100`; withdrawn/deleted → `0`.
- Missing/malformed stock → leave KIT stock unchanged.
- Absent-from-Samson → `0` only after complete full catalog traversal.
- Purchase `<=3000.00` → sale `×1.40`; `>3000.00` → sale `×1.26`; before-discount `×1.80`; minimum `×1.20` is calculated/reported but not sent unless KIT exposes a verified field.
- Do not use paid/larger runners, upload-artifact, large catalog snapshots, or photo archives.
- Webasyst v0.9.6 `SAMS-*` exclusion must be installed and verified before adding the scheduled trigger.

---

## File Structure

```text
samson-kit/
  README.md
  samson_kit/
    __init__.py
    config.py
    http.py
    rules.py
    samson_client.py
    kit_client.py
    mapper.py
    sync.py
    report.py
    cli.py
  tests/
    test_rules.py
    test_samson_client.py
    test_mapper.py
    test_sync.py
  state/
    last_sync.json
.github/workflows/samson-kit-sync.yml
```

`requirements.txt` stays lightweight; `requests` is already present.

---

### Task 1: Pure configuration, SKU, price and stock rules

**Files:**
- Create: `samson-kit/samson_kit/__init__.py`
- Create: `samson-kit/samson_kit/config.py`
- Create: `samson-kit/samson_kit/rules.py`
- Create: `samson-kit/tests/test_rules.py`

**Interfaces:**
- Produces: `Settings.from_env()`, `to_kit_sku()`, `calculate_prices()`, `calculate_stock()`.

- [ ] **Step 1: Write failing tests**

```python
import unittest
from decimal import Decimal
from samson_kit.rules import to_kit_sku, calculate_prices, calculate_stock

class RuleTests(unittest.TestCase):
    def test_sku(self):
        self.assertEqual(to_kit_sku("531863"), "SAMS-531863")

    def test_price_3000(self):
        p = calculate_prices(Decimal("3000.00"))
        self.assertEqual(p.sale, Decimal("4200.00"))
        self.assertEqual(p.old, Decimal("5400.00"))
        self.assertEqual(p.minimum, Decimal("3600.00"))

    def test_price_3000_01(self):
        self.assertEqual(calculate_prices(Decimal("3000.01")).sale, Decimal("3780.01"))

    def test_stock_positive(self):
        self.assertEqual(calculate_stock([2,3,4], active=True, withdrawn=False), 9)

    def test_stock_zero(self):
        self.assertEqual(calculate_stock([0,0,0], active=True, withdrawn=False), 100)

    def test_withdrawn(self):
        self.assertEqual(calculate_stock([5], active=False, withdrawn=True), 0)

    def test_missing_stock(self):
        self.assertIsNone(calculate_stock(None, active=True, withdrawn=False))

    def test_negative_stock(self):
        self.assertIsNone(calculate_stock([1,-1], active=True, withdrawn=False))
```

- [ ] **Step 2: Run and verify failure**

```bash
PYTHONPATH=samson-kit python -m unittest samson-kit/tests/test_rules.py -v
```

- [ ] **Step 3: Implement exact rules**

```python
# rules.py
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

Q = Decimal("0.01")

@dataclass(frozen=True)
class PriceDecision:
    sale: Decimal
    old: Decimal
    minimum: Decimal

def money(v):
    return Decimal(v).quantize(Q, rounding=ROUND_HALF_UP)

def to_kit_sku(code):
    code = str(code).strip()
    if not code:
        raise ValueError("empty Samson code")
    return "SAMS-" + code

def calculate_prices(purchase):
    purchase = Decimal(purchase)
    if purchase <= 0:
        return None
    sale_factor = Decimal("1.40") if purchase <= Decimal("3000.00") else Decimal("1.26")
    return PriceDecision(
        sale=money(purchase * sale_factor),
        old=money(purchase * Decimal("1.80")),
        minimum=money(purchase * Decimal("1.20")),
    )

def calculate_stock(parts, *, active, withdrawn):
    if withdrawn:
        return 0
    if not active or parts is None:
        return None
    values = [int(x) for x in parts]
    if any(x < 0 for x in values):
        return None
    total = sum(values)
    return total if total > 0 else 100
```

`config.py` validates non-empty `SAMSON_API_KEY` and `YANDEX_KIT_TOKEN` and defines bases `https://api.samsonopt.ru/v1` and `https://api.kit.yandex.net`, target warehouse `СПБ`.

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=samson-kit python -m unittest samson-kit/tests/test_rules.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add samson-kit/samson_kit samson-kit/tests/test_rules.py
git commit -m "feat: add Samson KIT business rules"
```

---

### Task 2: Secret-safe HTTP and Samson pagination

**Files:**
- Create: `samson-kit/samson_kit/http.py`
- Create: `samson-kit/samson_kit/samson_client.py`
- Create: `samson-kit/tests/test_samson_client.py`

**Interfaces:**
- Produces: `SafeSession.request_json()`, `parse_page()`, `SamsonClient.iter_categories()`, `SamsonClient.iter_skus()`.

- [ ] **Step 1: Write page parser tests**

```python
import unittest
from samson_kit.samson_client import parse_page

class PageTests(unittest.TestCase):
    def test_next_page(self):
        payload = [{"data":[{"sku":"531863"}],"meta":{"pagination":{"next":"https://api.samsonopt.ru/v1/sku/?pagination_page=2&api_key=SECRET"}}}]
        data, next_page = parse_page(payload)
        self.assertEqual(data[0]["sku"], "531863")
        self.assertEqual(next_page, 2)

    def test_last_page(self):
        data, next_page = parse_page({"data":[],"meta":{"pagination":{"next":None}}})
        self.assertEqual(data, [])
        self.assertIsNone(next_page)

    def test_invalid_page(self):
        with self.assertRaises(ValueError):
            parse_page({"wrong":[]})
```

- [ ] **Step 2: Run and verify failure**

```bash
PYTHONPATH=samson-kit python -m unittest samson-kit/tests/test_samson_client.py -v
```

- [ ] **Step 3: Implement bounded retry without secret leakage**

`SafeSession` uses one `requests.Session`, timeout `(10,60)`, max 4 attempts, retries transport errors and HTTP `429,500,502,503,504`, delays `1,2,4` seconds or numeric `Retry-After` capped at 30 seconds. Raised errors may contain only method, host/path and status; never query strings, Authorization headers or body objects containing secrets.

- [ ] **Step 4: Implement `parse_page()`**

```python
from urllib.parse import parse_qs, urlparse

def parse_page(payload):
    env = payload[0] if isinstance(payload, list) and len(payload) == 1 else payload
    if not isinstance(env, dict) or not isinstance(env.get("data"), list):
        raise ValueError("invalid Samson page envelope")
    next_url = (((env.get("meta") or {}).get("pagination") or {}).get("next"))
    if not next_url:
        return env["data"], None
    q = parse_qs(urlparse(next_url).query)
    page = (q.get("pagination_page") or [None])[0]
    if page is None or not str(page).isdigit():
        raise ValueError("invalid Samson next-page cursor")
    return env["data"], int(page)
```

- [ ] **Step 5: Implement category/SKU iterators**

Every request uses params:

```python
{
  "api_key": self.api_key,
  "response_format": "json",
  "pagination_page": page,
}
```

Paths: `/category/` and `/sku/`. Keep only numeric page ids in `seen_pages`; repeated page id raises. Natural `next=None` is the only successful completion condition.

- [ ] **Step 6: Run tests and commit**

```bash
PYTHONPATH=samson-kit python -m unittest samson-kit/tests/test_samson_client.py -v
git add samson-kit/samson_kit/http.py samson-kit/samson_kit/samson_client.py samson-kit/tests/test_samson_client.py
git commit -m "feat: add Samson API client"
```

---

### Task 3: Yandex KIT client

**Files:**
- Create: `samson-kit/samson_kit/kit_client.py`
- Create/extend: `samson-kit/tests/test_sync.py`

**Interfaces:**
- Produces exact methods for warehouses, variants, categories, characteristics, file uploads, product/variant creation, bulk price and stock updates.

- [ ] **Step 1: Write warehouse resolution test**

```python
from samson_kit.kit_client import resolve_exact_warehouse

def test_unique():
    assert resolve_exact_warehouse([{"id":"1","title":"СПБ"}], "СПБ") == "1"
```

Also test zero and duplicate exact matches raise `RuntimeError`.

- [ ] **Step 2: Implement proven KIT endpoints**

```text
GET  /v1/warehouses?status=ACTIVE&page=N&per_page=100
GET  /v1/variants?page=N&per_page=100
GET  /v1/categories?status=ACTIVE&page=N&per_page=100
POST /v1/categories
GET  /v1/characteristics?status=ACTIVE&page=N&per_page=100
POST /v1/characteristics
POST /v1/files
POST /v1/products
POST /v1/variants
POST /v1/variants/prices/bulk_update
POST /v1/variants/stocks/bulk_update
```

`index_samson_variants()` must filter exact `sku.startswith("SAMS-")` and map SKU to a list of matches so duplicates remain visible.

- [ ] **Step 3: Implement bulk chunks of 5000**

```python
for chunk in chunks(items, 5000):
    post("/v1/variants/prices/bulk_update", {"items": chunk})
```

Same for stocks.

- [ ] **Step 4: Run all tests and commit**

```bash
PYTHONPATH=samson-kit python -m unittest discover -s samson-kit/tests -v
git add samson-kit/samson_kit/kit_client.py samson-kit/tests/test_sync.py
git commit -m "feat: add Yandex KIT API client"
```

---

### Task 4: Normalize Samson fields and create new KIT card payloads

**Files:**
- Create: `samson-kit/samson_kit/mapper.py`
- Create: `samson-kit/tests/test_mapper.py`

**Interfaces:**
- Produces: `normalize_samson_record(raw)`, category chain, facet/characteristic mapping, barcode/media/cargo-box mapping.

- [ ] **Step 1: Write fixture-based mapper test**

Use a source fixture containing `sku`, `vendor_code`, `manufacturer`, `brand`, `name`, `description`, extended description, `category_list`, `barcode`, `weight`, `volume`, `facet_list`, `photo_list`, personal price, all stock fields, withdrawal flag/date. Assert no value is fabricated and SKU becomes `SAMS-*`.

- [ ] **Step 2: Implement source aliases conservatively**

Prefer actual Samson field names from the live `/sku/` payload. Support only aliases seen in official/current response. For each run, dry-run diagnostics report **key names only** from the first valid item so missing aliases can be added with a test; never dump values/full records.

- [ ] **Step 3: Stock normalization**

If the source exposes a stock list/collection, sum every location quantity from that list. Otherwise collect every documented scalar warehouse stock field present in the record, including ИДП, РЦ and in-transit. Any malformed or negative component makes stock unsafe (`None`) rather than 100.

- [ ] **Step 4: Category/characteristic policy**

Category matching key is `(normalized parent_id, normalized title)`. More than one match in same parent is ambiguous and skips that new card. Facets become KIT characteristics; reuse by normalized title + compatible type, otherwise create a TEXT characteristic. Existing products never get content rewritten.

- [ ] **Step 5: Image transfer**

For new cards only, stream each unique photo URL into `TemporaryDirectory`, reject files over 25 MiB, upload to `/v1/files`, attach as:

```python
{"type":"IMAGE","display_sequence":n,"image_id":file_id}
```

Image failure is a warning, not a whole-product failure.

- [ ] **Step 6: Run mapper tests and commit**

```bash
PYTHONPATH=samson-kit python -m unittest samson-kit/tests/test_mapper.py -v
git add samson-kit/samson_kit/mapper.py samson-kit/tests/test_mapper.py
git commit -m "feat: map Samson catalog data to KIT"
```

---

### Task 5: Sync orchestration, idempotency and mass-zero protection

**Files:**
- Create: `samson-kit/samson_kit/sync.py`
- Complete: `samson-kit/tests/test_sync.py`

**Interfaces:**
- Produces: `run_sync(settings, *, dry_run, create_limit, runtime_budget_seconds, samson_client=None, kit_client=None)`.

- [ ] **Step 1: Write fake-client tests for destructive cases**

Test all of these:

```text
existing active stock 9 -> SPB 9
existing active confirmed 0 -> SPB 100
withdrawn -> SPB 0
invalid stock -> unchanged
existing price threshold rules -> expected price update
existing content changes -> no content write
new source SKU -> one create
rerun same source -> no duplicate
non-SAMS KIT -> untouched
duplicate SAMS KIT SKU -> skip/report
incomplete Samson traversal -> no absent-item zeroing
complete traversal + KIT SAMS absent -> stock 0
other KIT warehouse quantities -> untouched
```

- [ ] **Step 2: Implement mandatory preflight**

Order:

```text
validate env
resolve unique active СПБ
index all KIT SAMS-* variants
load KIT categories/characteristics
start Samson catalog traversal
```

No writes before warehouse/index preflight succeeds.

- [ ] **Step 3: Existing-item update path**

For exactly one existing variant: compare current pricing/СПБ quantity and queue only changed values. Use bulk buffers, flush at 1000 items or end. Never write name, description, brand, barcode, category, image or characteristic for existing items.

- [ ] **Step 4: New-item path**

Ensure category chain, characteristics, one KIT product, one variant. Re-check exact SKU immediately before create; if it appeared, switch to existing behavior. `create_limit` limits only live new-card creation, not catalog scanning.

- [ ] **Step 5: Complete-catalog gate**

Keep `seen_skus`. Set `catalog_complete=True` only after natural final page. Only then compute:

```python
absent = set(existing_index) - seen_skus
```

Queue SPB `0` only for non-duplicate absent `SAMS-*` variants. Any pagination/request/validation failure means no absent-item zeroing.

- [ ] **Step 6: Runtime budget for free GitHub**

Default live budget `15000` seconds. When exceeded, stop starting **new card creation/image uploads**, flush queued safe updates, write report, and end. This may make the traversal incomplete; if so absent-item zeroing remains disabled. Subsequent manual runs skip already-created SKUs and progress further. No large pending catalog file is stored.

- [ ] **Step 7: Run sync tests and commit**

```bash
PYTHONPATH=samson-kit python -m unittest samson-kit/tests/test_sync.py -v
git add samson-kit/samson_kit/sync.py samson-kit/tests/test_sync.py
git commit -m "feat: add safe Samson KIT synchronization"
```

---

### Task 6: Reporting and CLI

**Files:**
- Create: `samson-kit/samson_kit/report.py`
- Create: `samson-kit/samson_kit/cli.py`
- Create: `samson-kit/README.md`
- Create: `samson-kit/state/last_sync.json`

**Interfaces:**
- CLI: `--dry-run`, `--live`, `--create-limit N`, `--runtime-budget N`.

- [ ] **Step 1: Add compact report fields**

Include timestamps, dry-run, catalog_complete, products_seen, new_created, price_updates, stock_updates, set_to_100, withdrawn_zeroed, absent_zeroed, duplicate_skus, invalid_prices, stock_anomalies, mapping_warnings, retries, errors, budget_exhausted, final status. No raw payloads/secrets.

- [ ] **Step 2: Add safe defaults**

Default is dry-run unless `--live`. Reject both flags together. `--create-limit` applies only to new live creates. Default runtime budget: 15000.

- [ ] **Step 3: Add read-only contract diagnostics**

Dry-run reads Samson category/SKU plus KIT warehouse/variant/category/characteristic endpoints and logs only sorted source field **names**, never values.

- [ ] **Step 4: Atomic state write**

Write temp JSON then `os.replace()` into `samson-kit/state/last_sync.json`. Initial committed file:

```json
{"status":"never_run"}
```

- [ ] **Step 5: Test and commit**

```bash
PYTHONPATH=samson-kit python -m unittest discover -s samson-kit/tests -v
git add samson-kit/README.md samson-kit/samson_kit/report.py samson-kit/samson_kit/cli.py samson-kit/state/last_sync.json
git commit -m "feat: add Samson sync CLI and reporting"
```

---

### Task 7: Add a **manual-only** GitHub workflow for testing/bootstrap

**Files:**
- Create: `.github/workflows/samson-kit-sync.yml`

**Interfaces:**
- Manual dispatch only at this stage. No cron yet.

- [ ] **Step 1: Create workflow**

```yaml
name: Samson to Yandex KIT

on:
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
      - run: PYTHONPATH=samson-kit python -m unittest discover -s samson-kit/tests -v
      - name: Run manual Samson sync
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
      - name: Commit compact report
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

- [ ] **Step 2: Verify low-cost constraints**

No `schedule`, no `upload-artifact`, no full source dump, no image commit, one standard runner, one concurrency group.

- [ ] **Step 3: Run tests and commit**

```bash
PYTHONPATH=samson-kit python -m unittest discover -s samson-kit/tests -v
git add .github/workflows/samson-kit-sync.yml
git commit -m "ci: add manual Samson KIT workflow"
```

---

### Task 8: Manual verification and initial full import

**Files:**
- Mapper/tests only if live API exposes additional real field aliases.

- [ ] **Step 1: Verify Webasyst blocker is cleared**

Install v0.9.6 and run a normal Webasyst sync. Acceptance:

```text
no SAMS-* not_found_on_site warnings
no SAMS-* price/stock/image/KIT-ID/create processing
normal non-SAMS sync still works
```

- [ ] **Step 2: User adds GitHub Actions secrets manually**

Repository → Settings → Secrets and variables → Actions:

```text
SAMSON_API_KEY
YANDEX_KIT_TOKEN
```

- [ ] **Step 3: Manual dry-run**

Dispatch:

```text
live=false
create_limit=20
```

Verify unique `СПБ`, catalog reads, field-key diagnostics, price/stock rules, zero writes, no secret leakage.

- [ ] **Step 4: Add only real source aliases revealed by dry-run**

For every added alias: write a failing mapper fixture test first, implement alias, rerun full suite.

- [ ] **Step 5: Manual live smoke**

Dispatch:

```text
live=true
create_limit=20
```

Inspect created cards: `SAMS-*`, hierarchy, text, brand, barcode, characteristics, images, prices, only `СПБ` stock, confirmed-zero → 100.

- [ ] **Step 6: Complete first full import**

Dispatch repeatedly with:

```text
live=true
create_limit=0
```

If `budget_exhausted=true`, rerun. Exact SKU idempotency means earlier cards are skipped. Finish when a full run reports `catalog_complete=true`, `budget_exhausted=false`, no systemic auth/warehouse error, and no remaining new creates on a confirmation run.

- [ ] **Step 7: Secret scan**

```bash
git grep -nE 'yakit_[A-Za-z0-9_-]+|api_key[[:space:]]*=[[:space:]]*[A-Za-z0-9]{20,}' -- . ':!docs/superpowers/*' && exit 1 || true
```

Inspect Actions logs too; no key/token or secret-bearing URL may appear.

---

### Task 9: Enable Monday 10:00 Moscow schedule only after verification

**Files:**
- Modify: `.github/workflows/samson-kit-sync.yml`

**Interfaces:**
- Adds weekly scheduled live run after all Task 8 acceptance criteria pass.

- [ ] **Step 1: Add schedule trigger**

Change workflow trigger to:

```yaml
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
```

- [ ] **Step 2: Add scheduled live step and keep manual branch separate**

```yaml
      - name: Run scheduled Samson sync
        if: github.event_name == 'schedule'
        env:
          SAMSON_API_KEY: ${{ secrets.SAMSON_API_KEY }}
          YANDEX_KIT_TOKEN: ${{ secrets.YANDEX_KIT_TOKEN }}
        run: PYTHONPATH=samson-kit python -m samson_kit.cli --live --runtime-budget 15000
```

Change the manual step condition to:

```yaml
if: github.event_name == 'workflow_dispatch'
```

- [ ] **Step 3: Verify cron/time and low-cost constraints**

`0 7 * * 1` is Monday 07:00 UTC = 10:00 Moscow. Confirm still one `ubuntu-latest` job, timeout 285 minutes, no artifacts/catalog dumps.

- [ ] **Step 4: Run tests and commit**

```bash
PYTHONPATH=samson-kit python -m unittest discover -s samson-kit/tests -v
git add .github/workflows/samson-kit-sync.yml
git commit -m "ci: enable weekly Samson KIT sync"
```

- [ ] **Step 5: Final acceptance**

The next manual full run and first scheduled run must both satisfy:

```text
existing SAMS: only prices/SPB stock changed
new Samson products: full card created
active confirmed zero: 100
withdrawn/deleted/complete-scan absent: 0
non-SAMS variants untouched
non-SPB warehouses untouched
no mass zero after incomplete source traversal
no secrets in repo/logs
```
