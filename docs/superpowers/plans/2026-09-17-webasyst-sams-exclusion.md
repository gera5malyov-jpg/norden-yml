# Webasyst SAMS Exclusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release `yandexkitsync` v0.9.6 so the existing Webasyst → Yandex KIT integration never reads, creates, prices, stocks, images, KIT-ID syncs, or zeroes any SKU beginning with `SAMS-`.

**Architecture:** Add one central SKU ownership policy and call it at every entry point that can mutate KIT or derive work queues. The highest-priority guard is the KIT-side planner loop, because that is where a KIT-only `SAMS-*` item could otherwise be classified as “missing on site” and zeroed. Secondary guards prevent site-side `SAMS-*` records from entering create/KIT-ID/image/background sync flows.

**Tech Stack:** PHP/Webasyst Shop-Script plugin, existing standalone PHP tests, ZIP packaging.

**Spec:** `docs/superpowers/specs/2026-09-17-samson-kit-sync-design.md`

## Global Constraints

- Every SKU whose trimmed value starts exactly with `SAMS-` is externally managed and must be ignored by Webasyst → KIT.
- The exclusion applies before matching, creation, price updates, stock updates, image processing, KIT-ID synchronization, and “missing on site → zero stock”.
- Non-`SAMS-*` behavior must remain unchanged.
- No Samson API or KIT secret is added to this plugin.
- The Samson GitHub workflow must not be enabled until this plugin release is deployed and verified.

---

### Task 1: Add a central external-SKU ownership policy

**Files:**
- Create: `yandexkitsync/lib/classes/shopYandexkitsyncSkuPolicy.class.php`
- Create: `yandexkitsync/tests/samson_sku_policy.php`

**Interfaces:**
- Produces: `shopYandexkitsyncSkuPolicy::isExternallyManaged($sku): bool`
- Consumed by later tasks in planner, sync and workers.

- [ ] **Step 1: Write the failing policy test**

```php
<?php
require_once __DIR__ . '/../lib/classes/shopYandexkitsyncSkuPolicy.class.php';

function check($condition, $message) {
    if (!$condition) {
        fwrite(STDERR, "FAIL: {$message}\n");
        exit(1);
    }
}

check(shopYandexkitsyncSkuPolicy::isExternallyManaged('SAMS-531863') === true, 'SAMS prefix must be externally managed');
check(shopYandexkitsyncSkuPolicy::isExternallyManaged('  SAMS-531863  ') === true, 'Whitespace must be ignored');
check(shopYandexkitsyncSkuPolicy::isExternallyManaged('sams-531863') === false, 'Prefix match is exact and case-sensitive');
check(shopYandexkitsyncSkuPolicy::isExternallyManaged('AF-31404392') === false, 'Normal site SKU must stay managed');
check(shopYandexkitsyncSkuPolicy::isExternallyManaged('') === false, 'Empty SKU is not an external SKU');

echo "OK\n";
```

- [ ] **Step 2: Run the test and verify it fails because the class does not exist**

Run:

```bash
php yandexkitsync/tests/samson_sku_policy.php
```

Expected: non-zero exit with missing `shopYandexkitsyncSkuPolicy.class.php`.

- [ ] **Step 3: Implement the minimal ownership policy**

```php
<?php

class shopYandexkitsyncSkuPolicy
{
    const EXTERNAL_PREFIX = 'SAMS-';

    public static function isExternallyManaged($sku)
    {
        $sku = trim((string) $sku);
        return $sku !== '' && strncmp($sku, self::EXTERNAL_PREFIX, strlen(self::EXTERNAL_PREFIX)) === 0;
    }
}
```

- [ ] **Step 4: Run the policy test**

Run:

```bash
php yandexkitsync/tests/samson_sku_policy.php
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add yandexkitsync/lib/classes/shopYandexkitsyncSkuPolicy.class.php yandexkitsync/tests/samson_sku_policy.php
git commit -m "feat: add external SAMS SKU policy"
```

---

### Task 2: Prevent planner zeroing/pricing/stocks for KIT-side `SAMS-*`

**Files:**
- Modify: `yandexkitsync/lib/classes/shopYandexkitsyncPlanner.class.php`
- Create: `yandexkitsync/tests/samson_planner_exclusion.php`

**Interfaces:**
- Consumes: `shopYandexkitsyncSkuPolicy::isExternallyManaged()`
- Produces: `shopYandexkitsyncPlanner::buildMultiWarehouse()` with no updates/warnings for externally managed variants.

- [ ] **Step 1: Write the behavioral planner regression test**

```php
<?php
require_once __DIR__ . '/../lib/classes/shopYandexkitsyncSelection.class.php';
require_once __DIR__ . '/../lib/classes/shopYandexkitsyncStockResolver.class.php';
require_once __DIR__ . '/../lib/classes/shopYandexkitsyncPriceCalculator.class.php';
require_once __DIR__ . '/../lib/classes/shopYandexkitsyncSkuPolicy.class.php';
require_once __DIR__ . '/../lib/classes/shopYandexkitsyncPlanner.class.php';

function check($condition, $message) {
    if (!$condition) {
        fwrite(STDERR, "FAIL: {$message}\n");
        exit(1);
    }
}

$variants = array(
    array('id'=>'kit-samson','sku'=>'SAMS-531863','status'=>'ACTIVE','stocks'=>array(array('warehouse_id'=>'spb','quantity'=>77))),
    array('id'=>'kit-normal','sku'=>'NORMAL-1','status'=>'ACTIVE','stocks'=>array(array('warehouse_id'=>'spb','quantity'=>5))),
);
$rules = array('spb'=>array(
    'warehouse_id'=>'spb',
    'title'=>'СПБ',
    'type_ids'=>array(1),
    'stock_ids'=>array(1),
));

$result = shopYandexkitsyncPlanner::buildMultiWarehouse($variants, array(), $rules);

check(count($result['price_updates']) === 0, 'No price updates expected');
check(count($result['stock_updates']) === 1, 'Only the normal missing SKU may be zeroed');
check($result['stock_updates'][0]['variant_id'] === 'kit-normal', 'SAMS variant must never enter stock updates');
check($result['stats']['missing_on_site'] === 1, 'SAMS variant must not count as missing on site');
foreach ($result['warnings'] as $warning) {
    check(!isset($warning['sku']) || $warning['sku'] !== 'SAMS-531863', 'No SAMS warning may be emitted by planner');
}

echo "OK\n";
```

- [ ] **Step 2: Run it and confirm the current planner fails by producing a zero-stock update for `SAMS-531863`**

Run:

```bash
php yandexkitsync/tests/samson_planner_exclusion.php
```

Expected: FAIL at the stock-update count or SAMS warning assertion.

- [ ] **Step 3: Add the earliest safe guard in the KIT variant loop**

Immediately after normalizing `$variant_id` and `$sku`, and after the existing invalid-id/empty-sku check, add:

```php
if (shopYandexkitsyncSkuPolicy::isExternallyManaged($sku)) {
    continue;
}
```

Do not increment `missing_on_site`, `matched`, warehouse stats, warnings, prices or stocks for the skipped variant.

- [ ] **Step 4: Run the planner test and the existing zero-price test**

```bash
php yandexkitsync/tests/samson_planner_exclusion.php
php yandexkitsync/tests/zero_purchase_price.php
```

Expected: both print `OK`.

- [ ] **Step 5: Commit**

```bash
git add yandexkitsync/lib/classes/shopYandexkitsyncPlanner.class.php yandexkitsync/tests/samson_planner_exclusion.php
git commit -m "fix: exclude SAMS variants from KIT planner"
```

---

### Task 3: Exclude site-side `SAMS-*` from sync indexing and product creation

**Files:**
- Modify: `yandexkitsync/lib/classes/shopYandexkitsyncSync.class.php`
- Modify: `yandexkitsync/lib/classes/shopYandexkitsyncProductCreateWorker.class.php`
- Create: `yandexkitsync/tests/samson_site_flow_exclusion.php`

**Interfaces:**
- Consumes: central SKU policy.
- Produces: filtered site map before missing-index work and a creation worker that returns a skip result for `SAMS-*`.

- [ ] **Step 1: Add a source-level regression test for the two required guards**

```php
<?php
function check($condition, $message) {
    if (!$condition) {
        fwrite(STDERR, "FAIL: {$message}\n");
        exit(1);
    }
}

$root = dirname(__DIR__);
$sync = file_get_contents($root . '/lib/classes/shopYandexkitsyncSync.class.php');
$create = file_get_contents($root . '/lib/classes/shopYandexkitsyncProductCreateWorker.class.php');

check(strpos($sync, 'shopYandexkitsyncSkuPolicy::isExternallyManaged') !== false, 'Sync must filter externally managed site SKUs');
check(strpos($create, 'shopYandexkitsyncSkuPolicy::isExternallyManaged') !== false, 'Create worker must reject externally managed site SKUs');
check(strpos($create, "'skipped_external'") !== false, 'Create worker must report external skips explicitly');

echo "OK\n";
```

- [ ] **Step 2: Run it and confirm failure**

```bash
php yandexkitsync/tests/samson_site_flow_exclusion.php
```

Expected: FAIL because the guards are absent.

- [ ] **Step 3: Filter `site_by_sku` immediately after repository loading in `shopYandexkitsyncSync`**

After:

```php
$site_by_sku = $repo->findByTypeIdsWithWarehouseSums(array_keys($type_map), $rules);
```

add:

```php
foreach (array_keys($site_by_sku) as $site_sku) {
    if (shopYandexkitsyncSkuPolicy::isExternallyManaged($site_sku)) {
        unset($site_by_sku[$site_sku]);
    }
}
```

This must execute before building `$missing_index` and before planner invocation.

- [ ] **Step 4: Make product creation skip an external SKU before any KIT lookup/write**

At the start of `processOne()` after trimming `$sku`, add `skipped_external` to the delta and return before cache/API/category/media logic:

```php
$delta = array(
    'created'=>0,
    'skipped_existing'=>0,
    'skipped_external'=>0,
    'skipped_no_image'=>0,
    'categories_created'=>0,
    'characteristics_created'=>0,
    'errors_count'=>0,
);
if (shopYandexkitsyncSkuPolicy::isExternallyManaged($sku)) {
    $delta['skipped_external'] = 1;
    return $delta;
}
```

Also add `skipped_external` to the `runJob()` per-item `$delta` initializer so aggregation is stable.

- [ ] **Step 5: Run tests**

```bash
php yandexkitsync/tests/samson_site_flow_exclusion.php
php yandexkitsync/tests/samson_planner_exclusion.php
php yandexkitsync/tests/zero_purchase_price.php
```

Expected: all `OK`.

- [ ] **Step 6: Commit**

```bash
git add yandexkitsync/lib/classes/shopYandexkitsyncSync.class.php yandexkitsync/lib/classes/shopYandexkitsyncProductCreateWorker.class.php yandexkitsync/tests/samson_site_flow_exclusion.php
git commit -m "fix: exclude SAMS SKUs from site sync and creation"
```

---

### Task 4: Exclude `SAMS-*` from KIT-ID and image background workers

**Files:**
- Modify: `yandexkitsync/lib/classes/shopYandexkitsyncKitIdWorker.class.php`
- Modify: `yandexkitsync/lib/classes/shopYandexkitsyncImageWorker.class.php`
- Create: `yandexkitsync/tests/samson_background_exclusion.php`

**Interfaces:**
- Consumes: central SKU policy.
- Produces: worker loops that skip `SAMS-*` before reading/writing site or KIT state.

- [ ] **Step 1: Write a focused regression test**

```php
<?php
function check($condition, $message) {
    if (!$condition) {
        fwrite(STDERR, "FAIL: {$message}\n");
        exit(1);
    }
}

$root = dirname(__DIR__);
$kitId = file_get_contents($root . '/lib/classes/shopYandexkitsyncKitIdWorker.class.php');
$image = file_get_contents($root . '/lib/classes/shopYandexkitsyncImageWorker.class.php');

check(substr_count($kitId, 'shopYandexkitsyncSkuPolicy::isExternallyManaged') >= 1, 'KIT-ID worker needs an external SKU guard');
check(substr_count($image, 'shopYandexkitsyncSkuPolicy::isExternallyManaged') >= 1, 'Image worker needs an external SKU guard');

echo "OK\n";
```

- [ ] **Step 2: Run it and confirm failure**

```bash
php yandexkitsync/tests/samson_background_exclusion.php
```

Expected: FAIL.

- [ ] **Step 3: Guard every normalized target SKU before worker processing**

In `shopYandexkitsyncKitIdWorker`, after each target SKU is normalized and before lookup/write work:

```php
if (shopYandexkitsyncSkuPolicy::isExternallyManaged($sku)) {
    continue;
}
```

In `shopYandexkitsyncImageWorker`, add the same guard in the variant analysis loop and in the final target/import loop. For loops whose variable is `$target_sku`, call the policy on `$target_sku`.

- [ ] **Step 4: Run all standalone plugin tests**

```bash
for test in yandexkitsync/tests/*.php; do php "$test" || exit 1; done
```

Expected: every test prints `OK` and exits 0.

- [ ] **Step 5: Commit**

```bash
git add yandexkitsync/lib/classes/shopYandexkitsyncKitIdWorker.class.php yandexkitsync/lib/classes/shopYandexkitsyncImageWorker.class.php yandexkitsync/tests/samson_background_exclusion.php
git commit -m "fix: exclude SAMS SKUs from background workers"
```

---

### Task 5: Release v0.9.6 and verify the ZIP before deployment

**Files:**
- Modify: `yandexkitsync/lib/config/plugin.php`
- Modify: `yandexkitsync/README.md`
- Create artifact: `yandexkitsync-v0.9.6.zip`

**Interfaces:**
- Produces: deployable plugin ZIP whose root directory is exactly `yandexkitsync/`.

- [ ] **Step 1: Change plugin version and document the ownership rule**

Set:

```php
'version' => '0.9.6',
```

Add a README note stating that any SKU beginning with `SAMS-` is managed externally and is ignored by all plugin synchronization paths.

- [ ] **Step 2: Run the complete plugin test suite**

```bash
for test in yandexkitsync/tests/*.php; do php "$test" || exit 1; done
```

Expected: all tests exit 0.

- [ ] **Step 3: Package the plugin**

From the directory containing `yandexkitsync/`:

```bash
zip -r yandexkitsync-v0.9.6.zip yandexkitsync -x '*.DS_Store' -x '__MACOSX/*'
```

- [ ] **Step 4: Inspect the ZIP structure and secret scan**

```bash
unzip -l yandexkitsync-v0.9.6.zip | head -40
unzip -p yandexkitsync-v0.9.6.zip yandexkitsync/lib/config/plugin.php | grep "0.9.6"
unzip -p yandexkitsync-v0.9.6.zip yandexkitsync/lib/classes/shopYandexkitsyncSkuPolicy.class.php | grep "SAMS-"
zipgrep -Ei 'yakit_|api_key[[:space:]]*=[[:space:]]*[A-Za-z0-9]{20,}|Authorization:[[:space:]]*Bearer[[:space:]]+[A-Za-z0-9_-]{20,}' yandexkitsync-v0.9.6.zip && exit 1 || true
```

Expected: root paths start with `yandexkitsync/`, version is 0.9.6, `SAMS-` policy exists, secret scan returns no match.

- [ ] **Step 5: Commit release metadata**

```bash
git add yandexkitsync/lib/config/plugin.php yandexkitsync/README.md
git commit -m "chore: release yandexkitsync 0.9.6"
```

- [ ] **Step 6: Deployment acceptance check before Samson import**

After user installs v0.9.6 in Webasyst, run one normal sync and inspect the log. Acceptance criteria:

```text
No SAMS-* SKU appears in not_found_on_site warnings.
No SAMS-* SKU appears in price update payloads.
No SAMS-* SKU appears in stock update payloads.
No SAMS-* SKU appears in image/KIT-ID/create job processing.
Existing non-SAMS sync still completes normally.
```

Do not enable the Samson scheduled workflow until these checks pass.
