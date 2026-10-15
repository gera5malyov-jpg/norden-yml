# Импорт универсальный (Shop-Script)

Independent, clean-room YML importer with external image links. Plugin ID: `univimport`.
Does not copy, modify, depend on, or read the separately licensed `yml` (YML PRO) plugin.

## Current 0.1.0 scope

- Separate supplier profiles, HTTPS YML URL, type/category/stock and SKU prefix.
- Stream parse `<offer>` from Yandex.Market YML, including sale price, basic quantity,
  name, description, vendorCode and `<picture>` (and `<images><image>`).
- Dry-run downloads a private immutable file snapshot, validates all offers,
  counts image URLs and duplicate offer IDs, and saves a 30-minute SHA-256 approval.
- Batch apply of up to 50 rows per POST and sequential progression in UI.
- New products are hidden by default. Existing products are updated **only** when
  mapped by this profile in `shop_univimport_map`, with matching product type and SKU ownership.
- External images are kept in the product `summary` (Краткое описание) as
  `[extimg]\nhttps://...\n[/extimg]` and NEVER downloaded to Webasyst image storage.
- Entirely separate profiles, storage and DB map from YML PRO.

## Important limitations (not full feature parity with commercial YML PRO)

- Only direct HTTPS, RUB/RUR YML, GET with no authentication/redirects; no FTP, email,
  archives, alternative XML formats, currency conversion or cron automation yet.
- `param` -> Shop-Script feature mapping, supplier category tree mapping,
  SKU variants and automatic category creation are not yet implemented.
- Does not automatically adopt existing unmapped products to prevent cross-supplier updates.
- `[extimg]` is data in `summary`, NOT native Shop-Script media records.
  A compatible storefront/KIT renderer must display these URLs as images.
- YML `<quantity>`, `<stock>` or `<count>` supported. No declared quantity means
  the stock is left unchanged, except `available="false"` means 0.
- No automatic zeroing of disappeared SKUs; no destructive changes/deletion.
- No one-click rollback; backup first. Partial writes possible if a batch fails.
- Not production-certified until verified in a matching Webasyst environment.

## Install

Copy only `webasyst/univimport` to `wa-apps/shop/plugins/univimport`.
Enable `univimport` through Webasyst plugin Installer/plugin registry.
Open Shop-Script > Импорт универсальный.
Never overwrite or alter `wa-apps/shop/plugins/yml`.

## Verification

`php -l` all PHP files and `php tests/test_import.php`.
Run a dry-run with a representative real YML before enabling `allow_write`.
Check SKU uniqueness, stock mapping, external image renderer on the actual storefront.
