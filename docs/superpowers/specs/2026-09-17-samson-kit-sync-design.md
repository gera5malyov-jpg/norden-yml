# Samson API → Yandex KIT: design specification

Date: 2026-09-17
Repository: `gera5malyov-jpg/norden-yml`
Status: approved design, implementation not started

## 1. Goal

Build an isolated weekly integration that imports the full Samsonopt catalog from the official Samson API into Yandex KIT and then maintains Samson-origin items without letting the existing Webasyst → KIT synchronization modify them.

Official Samson API documentation: `https://api.samsonopt.ru/v1/doc/index.html`.

The integration must be safe for the existing public GitHub repository and its free GitHub Actions usage: no large catalog snapshots, photo archives, or long-lived large artifacts are committed or stored.

## 2. Ownership boundary

All Samson-origin KIT variants use the SKU prefix `SAMS-`.

Example:

- Samson article: `531863`
- KIT SKU: `SAMS-531863`

The GitHub Samson integration is the only process allowed to create or modify `SAMS-*` products in KIT.

The existing Webasyst → KIT plugin must reject every `SAMS-*` SKU before any matching, creation, pricing, stock, image, KIT-ID, or "missing on site → zero stock" logic runs. This protection must be deployed before the first full Samson import.

No other KIT products are in scope for the Samson integration.

## 3. Repository layout

Add an isolated module under:

`samson-kit/`

Suggested internal responsibilities:

- Samson API client: authentication, pagination/batching, catalog data, prices, stocks.
- KIT API client: categories, products, variants, prices, stocks, lookup/indexing.
- Transformer: SKU prefixing, field mapping, price rules, stock rules.
- Sync orchestrator: new product creation, existing product updates, removed-product reconciliation.
- Reporting: compact JSON summary of the most recent run.
- Tests: pure unit tests for pricing, stock rules, SKU rules, deletion safety, and mapping.

Workflow:

`.github/workflows/samson-kit-sync.yml`

The implementation must reuse lightweight dependencies already compatible with this repository. `requests` is already present in the root `requirements.txt`; no heavy framework is required.

## 4. Secrets and configuration

Secrets must never be committed, printed, substituted into tracked source files, or written to the sync report.

GitHub Actions secrets:

- `SAMSON_API_KEY`
- `YANDEX_KIT_TOKEN`

The workflow passes secrets only as environment variables to the Python process.

Non-secret rules are committed in code/config and include:

- SKU prefix: `SAMS-`
- target KIT warehouse name: exact name `СПБ`
- schedule timezone rule: Monday 10:00 Moscow time = 07:00 UTC
- zero-stock fallback for active Samson products: 100
- price threshold: 3000 RUB inclusive
- price coefficients described below

The KIT warehouse must be resolved and validated by the exact name `СПБ` before writes begin. If zero or multiple exact matches are returned, the run must stop before stock writes instead of guessing a warehouse.

## 5. Schedule and execution model

Scheduled run:

- every Monday at 07:00 UTC / 10:00 Moscow time

Also provide `workflow_dispatch` for safe manual runs.

Manual mode must support a dry-run and an optional small item limit for initial validation. Scheduled mode always runs the full synchronization and must not use a test limit.

Use one standard `ubuntu-latest` job and a `concurrency` group so two Samson synchronizations cannot modify KIT in parallel.

Do not store the full Samson catalog, image archive, or full API payloads as GitHub artifacts or repository files. Process catalog data in bounded pages/batches and discard temporary data after use.

The implementation should use bounded retries with exponential/backoff-style delays for transient HTTP failures and respect API throttling/retry hints when supplied.

## 6. Source data and field policy

For new Samson products, transfer the maximum information that the official Samson API actually provides and KIT can accept, without inventing missing values.

Candidate data includes, when available:

- Samson article and source identifiers
- product name
- complete category hierarchy
- descriptions
- brand/manufacturer
- barcodes
- images
- characteristics/facets
- color/material and other properties
- product dimensions and weight
- package dimensions and weight
- country/origin data
- units
- other compatible Samson fields exposed by the API

Exact endpoint names and source-field names must be taken from the official Samson API v1 documentation during implementation; they must not be guessed.

If Samson provides a field that KIT does not support, omit that field from the KIT write and include a compact mapping warning/count in the run report. An unsupported optional field must not prevent creation of an otherwise valid product.

## 7. Category behavior

Reproduce the Samson category hierarchy in KIT for new products.

Before creating a category, resolve an existing equivalent in the expected parent scope to avoid duplicate category trees.

Create missing category levels as needed. Existing Samson product categories are not rewritten on later weekly runs.

## 8. New versus existing products

### New `SAMS-*` product

Create the KIT product/variant with as much supported Samson content as possible, including images and barcodes.

Apply the pricing and stock rules in this specification.

### Existing `SAMS-*` product

On normal weekly runs update only:

- price data
- stock on KIT warehouse `СПБ`

Do not rewrite its name, description, images, characteristics, barcodes, manufacturer, or category after initial creation.

Only send KIT update requests when the relevant price or stock value has actually changed.

## 9. Price rules

The source price is the user's personal/contract Samson purchase price returned by the authenticated Samson API.

Use decimal arithmetic, not binary floating-point arithmetic. Round monetary values to the precision required by KIT; when KIT accepts kopecks, round to two decimal places using normal commercial half-up rounding.

Rules:

- purchase price `<= 3000.00 RUB`: sale price = purchase price × `1.40`
- purchase price `> 3000.00 RUB`: sale price = purchase price × `1.26`
- old/before-discount price = purchase price × `1.80`
- minimum price = purchase price × `1.20`

If KIT exposes no compatible minimum-price field, do not substitute the value into another field; report that the minimum-price mapping is unsupported.

If a purchase price is missing, non-numeric, or `<= 0`, do not fabricate a sale price. Existing product price remains unchanged. For a new product, create it without derived pricing only if KIT permits that; otherwise skip creation of that item and report the precise reason so it can be retried on a later run.

## 10. Stock rules

For an active Samson product, the source stock is the sum of all Samson warehouse/location stock quantities returned for that product.

Only the KIT warehouse with exact name `СПБ` is modified. No Samson run may change stock on other KIT warehouses.

Rules:

1. Active Samson product and confirmed total stock `> 0` → KIT `СПБ` stock = confirmed total.
2. Active Samson product and confirmed total stock `= 0` → KIT `СПБ` stock = `100`.
3. Product explicitly marked by Samson as withdrawn/discontinued/deleted → KIT `СПБ` stock = `0`.
4. Product absent from a fully and successfully retrieved complete Samson catalog → KIT `СПБ` stock = `0`.
5. Stock data missing, malformed, or unavailable because of a source/API error → do not change that product's current KIT stock.

The value `100` is therefore a business fallback only for a successfully confirmed active product whose summed stock is exactly zero. It must never be used as an API-error fallback.

Negative source stock values, if encountered, must not be silently treated as a valid positive quantity. Record the anomaly and do not overwrite KIT stock for that item unless Samson documentation explicitly defines how such values should be interpreted.

## 11. Removed-product reconciliation and mass-zero protection

At the beginning of reconciliation, build/index the current KIT set of `SAMS-*` SKUs.

A KIT `SAMS-*` product can be considered absent from Samson only after the full current Samson catalog has been retrieved successfully to a known completion condition according to the official API pagination/response contract.

If any required catalog page fails, pagination terminates unexpectedly, response validation fails, or the run cannot prove catalog completeness:

- do not perform absent-item reconciliation;
- do not mass-zero products merely because they were not seen;
- continue only with individually safe updates whose source data is confirmed;
- mark the overall run as degraded/failed in the report.

Products explicitly returned by Samson with a documented withdrawn/deleted status may be set to zero individually even without absent-item reconciliation, provided that status was successfully retrieved and validated.

## 12. Idempotency and matching

SKU is the primary integration key.

- Samson article `A` maps deterministically to KIT SKU `SAMS-A`.
- Running the same source data twice must not create duplicate products or categories.
- Existing `SAMS-*` KIT variants are updated, not recreated.
- The integration must never claim or mutate non-`SAMS-*` KIT variants.

If duplicate `SAMS-*` SKUs already exist in KIT, the run must not guess which one to update. Record the duplicates and skip destructive writes for those SKUs until the ambiguity is resolved.

## 13. Error handling

A failure of one product must not normally terminate the whole catalog run.

Classify errors at least as:

- source authentication/configuration error: stop the run before writes;
- KIT authentication/configuration error: stop the run before writes;
- target warehouse resolution error: stop stock writes;
- transient HTTP error: bounded retry;
- invalid single-product data: skip that unsafe operation and continue;
- incomplete Samson catalog: disable absent-product zeroing;
- duplicate KIT SKU: skip that SKU and report;
- unsupported optional field: omit field and continue.

Never log API keys, Authorization headers, or full secret-bearing request objects.

## 14. Reporting and repository storage

Keep only a small sync summary, e.g. `samson-kit/state/last_sync.json`, containing no secrets and no full catalog payload.

Suggested counters/status fields:

- started/finished timestamps
- dry-run flag
- Samson products seen
- catalog completeness flag
- new products created
- existing products with price changes
- existing products with stock changes
- active-zero products set to 100
- withdrawn/deleted products set to 0
- absent-after-complete-scan products set to 0
- skipped invalid-price items
- duplicate KIT SKU count
- mapping-warning count
- API/retry/error counts
- final status

Do not commit full source data or photo files.

## 15. Webasyst plugin safety requirement

Before enabling the Samson workflow, update the existing Webasyst → KIT plugin so `SAMS-*` is excluded at the earliest possible point from all relevant paths.

The exclusion must cover at least:

- site-to-KIT matching
- KIT-to-site missing-item detection
- product creation
- price updates
- stock updates
- image processing
- KIT-ID synchronization
- any zero-stock reconciliation

This is a release-blocking prerequisite. The Samson scheduled workflow must not be enabled until the patched plugin has been deployed and verified.

## 16. Rollout sequence

1. Patch and deploy the Webasyst plugin exclusion for `SAMS-*`.
2. Add `SAMSON_API_KEY` and `YANDEX_KIT_TOKEN` to GitHub Actions Secrets manually in repository settings.
3. Implement and run local/unit tests with no live writes.
4. Run GitHub workflow manually in dry-run mode on a small sample.
5. Review source mappings, category paths, SKU prefixing, calculated prices, and summed stock.
6. Run a small live-write sample and verify resulting KIT cards and only the `СПБ` warehouse stock.
7. Run the first full manual import.
8. Review the sync summary and a sample of created/updated/zeroed products.
9. Enable/retain the Monday 07:00 UTC schedule.

## 17. Test requirements

At minimum, automated tests must cover:

- `531863` → `SAMS-531863`
- purchase price 3000.00 uses ×1.40
- purchase price 3000.01 uses ×1.26
- old price ×1.80
- minimum price ×1.20
- active total stock >0 passes summed stock
- confirmed active total stock 0 becomes 100
- withdrawn/deleted becomes 0
- stock API failure leaves KIT stock unchanged
- incomplete full catalog does not trigger absent-item zeroing
- complete catalog absence does trigger zero for previously known `SAMS-*`
- non-`SAMS-*` KIT variants are never touched
- duplicate `SAMS-*` KIT SKU is not destructively updated
- rerun with unchanged source data does not create duplicates or unnecessary writes

A live smoke test must be manual and limited before the first full import.

## 18. Success criteria

The integration is successful when:

- every eligible new Samson product can be created under deterministic `SAMS-*` SKU ownership;
- new cards carry the maximum compatible source information available from Samson;
- weekly runs update only prices and `СПБ` stock for existing Samson products;
- active confirmed-zero products receive stock 100;
- withdrawn/deleted/fully-confirmed-absent products receive stock 0;
- non-Samson KIT products and all non-`СПБ` KIT warehouses remain unchanged;
- the Webasyst plugin cannot modify `SAMS-*` products;
- source/API failures cannot cause accidental mass zeroing or fabricated 100-unit stock;
- no API secret is present in git history or logs;
- the workflow remains lightweight enough for the existing public repository and standard GitHub-hosted runner usage.
