# Riva → Yandex KIT

Isolated synchronization for the Riva dealer YML feed.

- Source URL is stored only in repository secret `RIVA_FEED_URL`.
- Yandex KIT token uses existing repository secret `YANDEX_KIT_TOKEN`.
- SKU ownership: `riva-<offer id>`. Riva's `Артикул` is not unique across color/configuration offers.
- New products: full initial content, including all source images and useful characteristics.
- Existing `riva-*` products: price + СПБ/МСК stock only.
- Price: top-level Riva YML `<price>`, copied without markup, matching the Liga Divanov integration.
- Availability source: top-level `<count>`; Riva's `available=true` is ignored because it is true for every offer.
- Default stock mode: 100 on СПБ and 100 on МСК when `count > 0`, otherwise 0, matching Liga Divanov.
- Optional `actual` stock mode can copy Riva's actual `count` to both managed warehouses.
- By default, brand-new zero-stock offers are not created. If an already imported Riva offer reaches zero stock, its managed KIT stocks are set to 0.
- A zero-stock offer is created automatically on a later run if its Riva `count` becomes positive.
- Missing offers from a complete feed are reconciled to 0 on СПБ and МСК.
- Weekly schedule: Monday 10:00 Moscow / 07:00 UTC after rollout.

Feed audit on 2026-09-18:
- 218,287 offers
- 955 categories
- 6,846 offers with `count > 0`
- 211,441 offers with `count = 0`
- 11,562 duplicated article keys; therefore article alone is unsafe as KIT SKU
