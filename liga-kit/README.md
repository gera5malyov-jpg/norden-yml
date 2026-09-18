# Liga Divanov → Yandex KIT

Isolated synchronization for the Liga Divanov YML feed.

- Source: `https://ligadivanov.ru/acrit.export/ym_vendormodel_2023.xml`
- SKU ownership: `liga-<vendorCode>`
- New products: full initial content from the feed, including all images
- Existing products: price + СПБ/МСК stock only
- Active stock: 100 on СПБ and 100 on МСК
- Unavailable/confirmed-absent stock: 0 on СПБ and 0 on МСК
- Price: copied from the feed without markup
- Schedule after rollout: Monday 10:00 Moscow / 07:00 UTC
- Secret: `YANDEX_KIT_TOKEN`

The synchronizer never intentionally modifies non-`liga-*` variants.
