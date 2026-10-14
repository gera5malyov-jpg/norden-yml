# Megasuppliers universal supplier engine

Version target: **1.1.0**.

## Safety model

- Webasyst remains the master catalog and supplier registry.
- Heavy parsing runs in GitHub Actions.
- Every supplier profile is checked with a mandatory dry-run before any future write mode.
- Duplicate final SKUs, negative prices/stock, feed-collapse guards and abnormal price changes block a run.
- Text-based tabular PDF import is supported and still passes the mandatory dry-run. Scanned/image-only or non-tabular PDFs are intentionally blocked until supplier-specific extraction rules exist.
- Production deployment is **manual only**. The deploy workflow no longer runs on push to main.
- The 1.1.0 bridge supports dry-run and a guarded apply mode. Apply remains disabled by default and requires the Webasyst `enable_writes` setting, a fresh successful dry-run of the same config, and an unchanged source SHA-256.

## Supplier profile

The plugin UI stores per-supplier source format, URL or GitHub Secret name, supplier article field, SKU prefix, brand, field mappings, price formulas, image fields and catalog rules.

Supported source formats: YML/XML, XLSX, CSV and text-based tabular PDF. For PDF, the configured supplier SKU and name columns must be present in a detected table. Scanned/image-only or non-tabular PDFs are blocked and require a supplier-specific extractor.

Private source URLs should be stored in a GitHub Actions secret. Webasyst passes only the secret name through workflow inputs. The workflow resolves the secret in GitHub.

## Price formulas

Price formulas use a restricted arithmetic parser. Supported variables:

- `supplier_price`
- `purchase_price`
- `price`
- `compare_price`
- `stock`

Only numeric constants and `+`, `-`, `*`, `/` are permitted. Python code/function calls are rejected.

## Webasyst → GitHub → Webasyst status

1. Webasyst saves a supplier profile.
2. `ImportRun` sends the config as base64 to `supplier-engine-dry-run.yml`.
3. GitHub loads the source and runs validation.
4. GitHub signs the result with `MEGASUPPLIERS_CALLBACK_SECRET` using HMAC-SHA256.
5. Public route `/megasuppliers-callback/` validates the signature and stores the result for the originating `request_id`.
6. The plugin UI polls `ImportStatus` and shows passed/blocked state.

No callback secret or GitHub token is sent as workflow input.

## Required settings

Webasyst plugin settings:

- `github_repo`
- `github_ref` (use `main` after merge)
- `github_token`
- `callback_secret`

GitHub repository secrets:

- `MEGASUPPLIERS_CALLBACK_SECRET` — same value as Webasyst callback secret.
- Supplier source URL secrets must use the `MEGASUPPLIERS_SOURCE_*` namespace, for example `MEGASUPPLIERS_SOURCE_LIGA`.
- `GOOGLE_SERVICE_ACCOUNT_JSON` is used only by CI to build the exact installable package from the checksum-pinned original package.

## Package build

`scripts/build_megasuppliers_package.py` builds the installable ZIP from the checksum-pinned 1.0.1 base package plus the reviewed 1.1.0 overlay. CI builds it and lints every PHP file using PHP 7.3, matching the production PHP generation previously verified on the server.

Building and testing the ZIP does not install it and does not change production.


## Mapping multiple warehouse stocks

The supplier profile editor supports an optional **Сопоставление нескольких складов** section.
Each row binds a source field (for example, a YML `param name="Riva СПБ"`) to
one actual Webasyst stock id. One supplier/SKU remains one product; the inventory
update only addresses the configured warehouses.

- If `rules.warehouses` is empty, the existing `webasyst.stock_id` and
  `mapping.stock` behavior is unchanged.
- If mappings are present, `rules.warehouses` is a list of objects such as
  `{"source_field":"Riva СПБ","stock_id":11}`. Each target stock id must exist and be unique.
- Values must be numeric or recognized availability booleans. Blank, missing, or
  negative mapped quantities **block the dry-run**, rather than silently writing zero.
- `rules.zero_other_stocks` is forbidden for mapped profiles; all unmanaged warehouses are preserved.
- `zero_if_missing` zeros only mapped warehouses for a supplier SKU absent
  from the feed, and does not disable availability at an unmanaged warehouse.
- `only_create_in_stock` checks the mapped warehouse quantities, not a supplier total.
- The source may contain other fields with city labels, but the exact keys must
  be inspected before enabling Apply. A supplier API endpoint that does not
  return supported YML/XML, CSV or XLSX must first be adapted.
- The production plugin is never installed by a push. Run the manual deploy
  workflow **only after** PR validation and a dry-run against the actual supplier feed.
