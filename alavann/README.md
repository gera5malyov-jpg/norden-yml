# Alavann -> YML Pro (six bathroom furniture categories only)

Public Alavann category-page / sitemap product scraper. The feed **only** includes products with URL paths in these six directories:

1. https://alavann-shop.ru/mebel/cupboards/
2. https://alavann-shop.ru/mebel/komody/
3. https://alavann-shop.ru/mebel/zerkala-v-vannuyu/
4. https://alavann-shop.ru/mebel/stand/
5. https://alavann-shop.ru/mebel/set/
6. https://alavann-shop.ru/mebel/mirror-cupboard/

Product pages outside these paths, including screens, taps, towel rails, sinks, and any other sections, are excluded.

- Permanent YML URL: https://raw.githubusercontent.com/gera5malyov-jpg/norden-yml/main/alavann/alavann.yml
- Inputs: all pages (including pagination) in the six allowed listing categories, plus **only** sitemap product URLs within those same categories.
- Refresh: every 14 days, next scheduled from 30 October 2026, via GitHub Actions. Supports manual workflow_dispatch and code-update runs.
- Product fields: original article as YML offer id/vendorCode/model, RUB price, availability, description, specifications, all gallery image HTTPS links (images stay hosted on source).
- Safety: publishing is blocked on missing expected category, duplicate SKU, too few valid products, listing failure, severe category-scoped feed shrink, or severe category-scoped SKU loss.
- The old entire-site catalog was intentionally narrowed. Safety comparisons exclude previously published products from unrelated categories.
- YML Pro: match by vendorCode and import photos as remote URLs; configure missing-product behavior separately if needed.
