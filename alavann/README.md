# Alavann -> YML Pro
Independent publicly accessible Alavann site parser (no API access needed).
- Input: https://alavann-shop.ru/sitemap.xml and product pages with JSON-LD.
- Output: https://raw.githubusercontent.com/gera5malyov-jpg/norden-yml/main/alavann/alavann.yml
- Frequency: 14-day cadence anchored 19 Oct 2026, via GitHub Actions. Manual workflow_dispatch supported.
- Includes real SKUs as vendorCode/model/offer id, prices in RUB, actual availability, descriptions, specs, seller category hierarchy, photos as source HTTPS links.
- No made-up wholesale prices/stock figures. Images are never uploaded or stored.
- Fails without overwriting the previous feed when catalog size drops >20%, fewer than 200 products appear, or >10% pages fail. A temporary file is generated and atomically replaced only on success.
- Set YML Pro importer to read the URL, match on vendorCode, set product photos to URL mode (rather than binary copies).
