# Megasuppliers universal supplier engine

Version target: **1.1.0**.

## Safety model

- Webasyst remains the master catalog and supplier registry.
- Heavy parsing runs in GitHub Actions.
- Every supplier profile is checked with a mandatory dry-run before any future write mode.
- Duplicate final SKUs, negative prices/stock, feed-collapse guards and abnormal price changes block a run.
- Generic PDF import is intentionally blocked until supplier-specific extraction rules exist.
- Production deployment is **manual only**. The deploy workflow no longer runs on push to main.
- The 1.1.0 bridge supports dry-run and a guarded apply mode. Apply remains disabled by default and requires the Webasyst `enable_writes` setting, a fresh successful dry-run of the same config, and an unchanged source SHA-256.

## Supplier profile

The plugin UI stores per-supplier source format, URL or GitHub Secret name, supplier article field, SKU prefix, brand, field mappings, price formulas, image fields and catalog rules.

Supported source formats: YML/XML, XLSX and CSV. PDF requires a supplier-specific adapter.

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
