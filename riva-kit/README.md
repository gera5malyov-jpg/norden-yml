# Riva → Yandex KIT

Isolated synchronization for the Riva dealer YML feed.

- Source URL is stored only in repository secret `RIVA_FEED_URL`.
- Yandex KIT token uses existing repository secret `YANDEX_KIT_TOKEN`.
- KIT SKU/article is `riva-<offer id>` from the Riva feed, for example `riva-1290597`.
- Riva repeats the same supplier article across colors/configurations, so `offer id` is used to keep every variant unique.
- Original supplier `Артикул` is preserved as a separate product characteristic; technical `offer id` is also stored as `ID предложения Riva`.
- New products: full initial content, including all source images and useful characteristics.
- Existing Riva products: price + СПБ/МСК stock only.
- The top-level Riva YML `<price>` is the purchase price.
- KIT price before discount: purchase × 1.80.
- KIT price with discount: purchase × 1.26.
- Desired KIT minimum price: purchase × 1.20. The public KIT API currently exposes only `price` and `manual_discount_price`; minimum price requires a separate supported import/UI route.
- Availability source is top-level `<count>`; Riva's `available=true` is ignored because it is true for every offer.
- Stock rule on both managed warehouses:
  - if `count > 0`: write the actual Riva total `count` to both СПБ and МСК;
  - if `count = 0`: write 100 to СПБ and 100 to МСК.
- Zero-stock offers are therefore created too; initial import covers the whole feed.
- If an offer disappears from the feed entirely, its stock is left unchanged; only explicit Riva `count` controls stock.
- Batch import is supported with `--skip-items` and `--max-items` so the large source feed can be loaded safely in parts.
- Weekly schedule: Monday 10:00 Moscow / 07:00 UTC after rollout.

Feed audit on 2026-09-18:
- 218,287 offers
- 955 categories
- 6,846 offers with `count > 0`
- 211,441 offers with `count = 0`
- 11,814 distinct Riva articles
- 11,562 article values occur in multiple offer rows because colors/configurations share an article
