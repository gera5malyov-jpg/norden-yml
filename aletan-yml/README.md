# Aletan -> YML Pro (weekly)

Independent supplier feed generator. No API access or writes to KIT or Webasyst.

- Permanent YML link: https://raw.githubusercontent.com/gera5malyov-jpg/norden-yml/main/aletan-yml/aletan.yml
- Source: https://aletan.ru/upload/aletan_catalog_export.csv
- Schedule: GitHub Actions every Friday at 03:17 UTC, every **7 days**, plus manual workflow_dispatch.
- Workflow: .github/workflows/aletan-yml-weekly.yml
- Original article: <model> and param "Оригинальный артикул производителя".
- YML Pro matching field: <vendorCode>. For source articles shared by multiple separate items, a deterministic short URL hash suffix is appended, otherwise variants collide. All 851 current source product URLs remain separate offers.
- Price: source <price> in RUB is the supplier's published catalog price, not a verified wholesale/purchase price. The source CSV's wholeprice column is empty. No wholesale prices are invented.
- Availability: copied from the supplier CSV, with <count> set to zero on unavailable products.
- Images: source URLs in <picture>. No image binary uploads to GitHub/KIT.
- Safety: minimum item count, strict required fields, unique SKU and category IDs, loss protection and XML verification before publication.
- Legacy Aletan KIT and direct Webasyst sync workflows and script entrypoints are disabled. Existing records already in KIT are NOT deleted. YML Pro should be configured separately by the shop administrator for desired treatment of missing products.
