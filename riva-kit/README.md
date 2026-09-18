# Riva → Yandex KIT

Isolated synchronization for the Riva dealer YML feed.

- Source URL is stored only in repository secret `RIVA_FEED_URL`.
- Yandex KIT token uses existing repository secret `YANDEX_KIT_TOKEN`.
- KIT SKU/article is the exact Riva `Артикул` value. No `offer id` is substituted into the article.
- Riva legitimately repeats one article across different color/configuration offers. These variants keep the same article; synchronization distinguishes them technically by Riva `offer id`, barcode, and exact offer name.
- Technical Riva `offer id` is stored as characteristic `ID предложения Riva` and is not used as the visible article.
- New products: full initial content, including all source images and useful characteristics.
- Existing Riva products: price + СПБ/МСК stock only.
- Price: top-level Riva YML `<price>`, copied without markup, matching the Liga Divanov integration.
- Availability source: top-level `<count>`; Riva's `available=true` is ignored because it is true for every offer.
- Default stock mode: 100 on СПБ and 100 on МСК when `count > 0`, otherwise 0, matching Liga Divanov.
- Optional `actual` stock mode can copy Riva's actual `count` to both managed warehouses.
- Missing previously imported offers from a complete feed are reconciled to 0 on СПБ and МСК by technical offer id.
- Weekly schedule: Monday 10:00 Moscow / 07:00 UTC after rollout.

Feed audit on 2026-09-18:
- 218,287 offers
- 955 categories
- 6,846 offers with `count > 0`
- 211,441 offers with `count = 0`
- 11,814 distinct Riva articles
- 11,562 article values occur in multiple offer rows because colors/configurations share an article
