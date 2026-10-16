# Deccon public catalog scraper for Webasyst YML Pro

GitHub workflow: \`.github/workflows/deccon-yml-weekly.yml\`.
Refresh: every Sunday UTC 04:12, also manually or on code changes.
Public source: https://deccon.ru/catalog/ and EVERY pagination page, not stale 2024 sitemap.
Unique external offer ID: deccon-<Bitrix numeric product id>.
Original article if present: <vendorCode> from Product structured data.
Attributes: factual technical details, including mechanisms, case material, weight and clock dimensions, plus actual detail-page table fields.
Photos: all genuine original gallery images under /upload/iblock/ or /upload/uf/ (not resized/cache/webp previews). They remain remote image URLs.
Photo watermark note: if the origin JPEG already contains a baked-in watermark, this parser does NOT erase pixels. Separate clean authorized source images are required for that.
Supplier: site company/address/phones/dealer information, source product URL, ad copy are removed. Genuine product model and brand in name remain.
Safety: no import of price, quantity, available or stock; never pretend displayed retail prices are procurement prices. This is a CONTENT-ONLY intermediate YML.
The source site's schema InStock may be inconsistent with "Под заказ", so stock is deliberately excluded. Do not update stocks in Webasyst with this feed.
Mass loss or failures block publication; old YML is retained on failures. No older KIT/Webasyst integrations are modified.

After the first successful workflow completes, the permanent content URL is:
https://raw.githubusercontent.com/gera5malyov-jpg/norden-yml/main/deccon/catalog-content.yml

For YML Pro: use this feed to update descriptions, technical attributes and image URLs only. A price-less feed may need a private pricing/stock join before the actual importer accepts it. Match existing products by external ID consistently, or original article when present.
