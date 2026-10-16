# College-chair.ru - YML Pro catalog content

This directory separates public website content from private Excel prices and stock.

- Source: https://college-chair.ru/ ; five categories 7, 8, 16, 17, 18.
- Every SKU and color is a separate offer.
- Stable ID: college-<product numeric id>; original supplier article: vendorCode and model.
- Specifications from each product's characteristics table, including materials, mechanisms, colors and sizes.
- Images: original-size remote source URLs; no media files stored in KIT or Webasyst.
- Vendor's name, brand, contacts, promotional/dealer text, ratings and cross-selling are not exported in product descriptions or parameters.
- Photo URLs technically reveal the original image server. Hiding host requires separate authorized image hosting.
- IMPORTANT: No price, available, or quantity data in this intermediate content feed. Never enable price or stock import with this file.
- Depending on YML Pro validation, this price-less intermediate YML might not be directly importable until Excel data is merged.
- All five categories required; duplicate articles, missing photos, HTTP errors, and >20% unexpected catalog shrink fail the job safely.
- GitHub Actions weekly cron, push, or manual dispatch.

Expected URL AFTER first successful GitHub Actions run:
https://raw.githubusercontent.com/gera5malyov-jpg/norden-yml/main/college-chair/catalog-content.yml

Next stage: fetch attachment from Gmail, join verified prices/stocks by exact vendorCode, publish final college-chair/catalog.yml with all mandatory price and stock fields. Never zero catalog data from a partial/failed Excel.
