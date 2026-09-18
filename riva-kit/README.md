# Riva → Yandex KIT

Isolated synchronization for the Riva dealer YML feed.

- Source URL is stored only in repository secret `RIVA_FEED_URL`.
- Yandex KIT token uses existing repository secret `YANDEX_KIT_TOKEN`.
- KIT SKU/article is the exact Riva `Артикул` value. No `offer id` is substituted into the article.
- Riva legitimately repeats one article across different color/configuration offers. These variants keep the same article; synchronization distinguishes them technically by Riva `offer id`, barcode, and exact offer name.
- Technical Riva `offer id` is stored as characteristic `ID предложения Riva` and is not used as the visible article.
- New products: full initial content, including all source images and useful characteristics.
- Existing Riva products: price + СПБ/МСК stock only.
- The top-level Riva YML `<price>` is the purchase price.
- KIT price before discount: purchase × 1.80.
- KIT price with discount: purchase × 1.26.
- KIT minimum price: purchase × 1.20. The exact writable KIT API field is verified on a Riva smoke item before full rollout.
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
