# Liga Divanov YML → Yandex KIT: design specification

Date: 2026-09-18  
Repository: `gera5malyov-jpg/norden-yml`  
Status: approved design, implementation not started

## 1. Goal

Build an isolated GitHub Actions integration that imports products from the Liga Divanov YML feed into Yandex KIT and then maintains only price and stock for those imported products.

Source feed:

`https://ligadivanov.ru/acrit.export/ym_vendormodel_2023.xml`

The integration must be independent from the existing Samson integration and from the Webasyst → KIT synchronization.

## 2. Source ownership and SKU namespace

Every Liga Divanov offer is owned by this integration through the SKU prefix:

`liga-`

The source article is taken from `vendorCode`.

Example:

- feed `vendorCode`: `109775`
- KIT SKU: `liga-109775`

The integration may create and update only KIT variants whose SKU starts with `liga-`.

It must never modify:

- `SAMS-*`
- ordinary Webasyst-managed SKUs
- products from any other integration

One YML `<offer>` maps to one KIT product/variant. `group_id` is source metadata and is not used to merge different vendorCodes into one KIT variant.

## 3. Observed feed structure

The source feed was probed on 2026-09-18 through GitHub Actions.

Observed current size:

- offers: about 4,918
- categories: 14

Observed offer fields include:

- offer attributes: `id`, `available`, `group_id`
- `url`
- `price`
- `currencyId`
- `count`
- `categoryId`
- `picture`
- `vendor`
- `name`
- `description`
- `manufacturer_warranty`
- `country_of_origin`
- `barcode`
- `vendorCode`
- `weight`
- `dimensions`
- repeated `param` elements

The `picture` element currently contains multiple image URLs in one comma-separated text value. The importer must split this value and treat each resulting URL as a separate source image.

## 4. Repository layout

Create a separate module:

`liga-kit/`

Responsibilities should be split into focused files:

- feed client/parser: download and stream/parse YML
- normalized Liga offer model
- KIT API client or a narrowly shared KIT transport layer if safe to reuse
- mapper: feed fields → KIT payload
- sync orchestrator
- price/stock reconciliation
- reporting
- automated tests

Workflow:

`.github/workflows/liga-kit-sync.yml`

The Liga implementation must not import or call Samson business logic.

## 5. Secrets and configuration

Reuse the existing Yandex KIT credential:

- `YANDEX_KIT_TOKEN`

No Liga credential is currently required because the feed is public.

Non-secret configuration:

- feed URL: `https://ligadivanov.ru/acrit.export/ym_vendormodel_2023.xml`
- SKU prefix: `liga-`
- target warehouses: exact names `СПБ` and `МСК`
- active stock per target warehouse: `100`
- schedule: Monday 10:00 Moscow = Monday 07:00 UTC
- price source: feed `<price>` exactly as supplied

The sync must resolve exactly one KIT warehouse named `СПБ` and exactly one named `МСК` before stock writes. Missing or ambiguous warehouse resolution must stop stock writes rather than guessing.

## 6. Schedule and execution model

Scheduled run:

- every Monday at 07:00 UTC / 10:00 Moscow time
- cron: `0 7 * * 1`

Also support `workflow_dispatch` for manual validation and recovery runs.

Use a dedicated concurrency group so two Liga sync runs cannot write simultaneously.

The first rollout is manual:

1. automated tests
2. full dry-run
3. live smoke test on exactly 3 new Liga products
4. verification of created cards
5. first full live import
6. enable/retain weekly schedule

## 7. New product behavior

For a new `liga-*` SKU, create the KIT product/variant with the maximum compatible data from the feed.

Map when available:

- name
- description
- category
- vendor / brand
- barcode
- source URL where KIT supports it
- weight
- dimensions
- country of origin
- manufacturer warranty
- source parameters
- all source images
- source price
- stock on `СПБ`
- stock on `МСК`

Do not invent values that are absent from the feed.

Unsupported optional fields are omitted and counted as mapping warnings; they must not prevent creation of an otherwise valid product.

## 8. Existing product behavior

After initial creation, normal weekly runs for existing `liga-*` products update only:

- price
- stock on `СПБ`
- stock on `МСК`

Do not overwrite after initial creation:

- name
- description
- images
- characteristics
- category
- barcode
- vendor/brand
- dimensions
- weight
- other content fields

Only send KIT writes when a price or target stock value actually changed.

## 9. Price rules

The source price is the YML `<price>` value.

The KIT selling price must equal the feed price exactly, subject only to KIT's required numeric precision/rounding rules.

No markup coefficient is applied.

If price is missing, invalid, non-numeric, or `<= 0`:

- existing Liga product: leave current KIT price unchanged and report warning
- new Liga product: skip live creation if KIT requires a valid positive price; otherwise create only if KIT accepts the payload safely

No derived old price or minimum price is created unless a future requirement explicitly defines one.

## 10. Stock rules

For every active Liga offer:

- KIT warehouse `СПБ` = `100`
- KIT warehouse `МСК` = `100`

The feed's current `<count>` value does not control KIT stock under this business rule.

A Liga product becomes zero on both managed warehouses when either condition is true:

1. the offer is present in a successfully retrieved complete feed with `available="false"`
2. the previously known `liga-*` SKU is absent from a successfully retrieved complete feed

Then:

- `СПБ` = `0`
- `МСК` = `0`

The Liga sync must not change any other KIT warehouses.

## 11. Feed completeness and mass-zero protection

Absent-item zeroing is allowed only after the full Liga feed has been downloaded and parsed successfully.

If the feed download fails, XML is malformed, parsing terminates early, or completeness cannot be established:

- do not zero SKUs merely because they were not seen
- do not run absent-item reconciliation
- keep existing KIT stock unchanged unless a specific offer was safely parsed
- mark the run degraded/failed

This protection prevents a broken or partial feed from setting all `liga-*` stock to zero.

## 12. Image handling

The importer must preserve every source image for a new product.

Current Liga feed format places multiple image URLs in one `<picture>` element separated by commas.

Required normalization:

1. collect every `picture` element
2. split each text value on commas
3. trim whitespace
4. discard empty entries
5. preserve source order
6. de-duplicate exact repeated URLs without reordering

For new products, image preparation is strict:

- if the feed yields N source image URLs, all N must be successfully prepared/uploaded before the product is considered successfully created
- a partial N−1 image set must not be treated as success
- the failed item remains retryable on the next run

Existing `liga-*` products do not receive routine image updates after initial creation.

## 13. Category behavior

Use the feed's `categoryId` and `<categories>` section to resolve category names.

Create or reuse the corresponding KIT category for a new Liga product.

The current feed categories are flat; if parent relationships appear later, the parser should preserve them rather than flattening them.

Existing Liga products do not have their category rewritten on weekly runs.

Category creation must avoid duplicates by resolving an equivalent existing category before creating a new one.

## 14. Characteristics and dimensions

Every feed `<param name="...">value</param>` is a candidate characteristic.

Examples observed include:

- fabric
- width
- depth
- height
- sleeping-place dimensions
- color
- frame material
- mechanism
- assembly requirement
- package descriptions and package dimensions
- collection
- filling
- seat dimensions

Duplicate params with the same name/value must be de-duplicated for KIT payload construction.

If KIT has multiple ambiguous characteristics with the same visible name, the importer must not choose a random characteristic ID. It should skip that ambiguous mapping and report a warning.

Top-level YML `weight` and `dimensions` should map to KIT dimensional fields where the API contract supports them.

## 15. Matching and idempotency

`vendorCode` is the source business key.

Deterministic mapping:

`vendorCode A` → KIT SKU `liga-A`

The same feed imported twice must not create duplicate products.

Existing exact `liga-*` SKU matches are updated, not recreated.

If KIT already contains duplicate variants with the same `liga-*` SKU:

- do not guess which one to update
- skip destructive writes for that SKU
- report the duplicate

## 16. Existing Webasyst and Samson isolation

Liga products must be protected from other integrations.

The existing Webasyst → KIT sync must ignore `liga-*` using the same early-exclusion principle already used for `SAMS-*`, covering at least:

- matching
- creation
- price update
- stock update
- image processing
- KIT-ID synchronization
- missing-on-site zeroing

Samson code must not claim, update, zero, or otherwise modify `liga-*`.

Liga code must not claim, update, zero, or otherwise modify `SAMS-*`.

Deploying the `liga-*` Webasyst exclusion is a release-blocking prerequisite before the first full Liga live import.

## 17. Error handling

Stop the run before unsafe writes for:

- KIT authentication failure
- warehouse resolution failure
- unrecoverable feed download failure
- malformed/incomplete feed where completeness cannot be established

Retry transient HTTP/network failures with bounded retries.

For single-product errors:

- record the SKU and reason
- skip the unsafe operation
- continue the catalog run where safe

Never print Authorization headers or `YANDEX_KIT_TOKEN`.

## 18. Reporting

Produce a compact JSON run report containing no secrets and no full feed payload.

Suggested fields:

- start/finish timestamp
- dry-run flag
- offers parsed
- catalog completeness
- active offers
- unavailable offers
- new products created
- existing products with price changes
- existing products with СПБ stock changes
- existing products with МСК stock changes
- absent products zeroed
- unavailable products zeroed
- invalid price count
- image preparation/upload failures
- duplicate `liga-*` SKU count
- ambiguous characteristic warning count
- API/retry/error counts
- final status

Do not commit the 28 MB source XML or image files to the repository.

## 19. Automated test requirements

At minimum, tests must cover:

- `vendorCode=109775` → `liga-109775`
- feed price `87990` → KIT price `87990`
- active offer → СПБ 100 and МСК 100
- `available="false"` → СПБ 0 and МСК 0
- absent SKU after complete feed → СПБ 0 and МСК 0
- incomplete/broken feed does not trigger absent-item zeroing
- comma-separated `picture` value is split into all URLs
- multiple `picture` elements are combined in order
- duplicate image URLs are de-duplicated without reordering
- image failure prevents partial new-product success
- repeated `param` handling is deterministic
- duplicate identical params do not create duplicate values
- ambiguous KIT characteristic name is skipped, not guessed
- existing `liga-*` product updates only price and managed stock
- existing `liga-*` content fields remain unchanged
- non-`liga-*` KIT variants are never touched
- duplicate `liga-*` KIT SKU is skipped safely
- rerunning unchanged feed does not create duplicates or unnecessary writes

## 20. Rollout sequence

1. Add `liga-*` exclusion to the Webasyst → KIT plugin and verify it.
2. Implement parser/model/rules using TDD.
3. Implement KIT mapping and category/characteristic behavior.
4. Implement strict image handling.
5. Implement existing-product price/stock-only update path.
6. Implement complete-feed absent reconciliation.
7. Run full automated test suite.
8. Run a full dry-run against the current feed and KIT.
9. Run a live smoke test for exactly 3 new Liga offers.
10. Verify SKU, price, both warehouse stocks, images, description, characteristics and category.
11. Run the first full live Liga import.
12. Review summary and spot-check created products.
13. Keep the scheduled Monday 07:00 UTC workflow enabled.

## 21. Success criteria

The integration is successful when:

- every eligible feed offer maps deterministically to one `liga-*` KIT SKU
- new products receive the maximum compatible feed content
- source price is transferred exactly as supplied
- active products have 100 on СПБ and 100 on МСК
- unavailable or confirmed-absent products have 0 on СПБ and 0 on МСК
- existing Liga products receive only price and managed-stock updates after creation
- every new product receives its complete source image set or is left retryable
- broken/incomplete feed retrieval cannot mass-zero Liga products
- Webasyst and Samson cannot modify `liga-*`
- Liga cannot modify non-`liga-*`
- no secret or full source feed is committed to git
- weekly synchronization runs every Monday at 10:00 Moscow time
