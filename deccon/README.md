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
Availability: each offer has available="true" only for explicit website label "Есть в наличии"; available="false" for "Под заказ", lead-time manufacture, "Нет в наличии" and discontinued. Each offer also has param name="Наличие" with a Russian status. We DO NOT take schema.org InStock as truth because items made to order are frequently marked InStock there.
Safety: no import of price or numeric quantity. Do not mistake site's retail prices for purchase prices or an available flag for confirmed warehouse stock units. This is a CONTENT+AVAILABILITY intermediate YML.
Webasyst: map the offer available attribute to availability as appropriate in YML Pro, but leave purchasing/pricing and numeric stock quantity mappings switched OFF; no 1-item inventory is fabricated. If website text becomes unrecognizable, old YML stays and the workflow fails.
Mass loss or failures block publication; old YML is retained on failures. No older KIT/Webasyst integrations are modified.

After the first successful workflow completes, the permanent content URL is:
https://raw.githubusercontent.com/gera5malyov-jpg/norden-yml/main/deccon/catalog-content.yml

For YML Pro: use this feed to update descriptions, technical attributes and image URLs only. A price-less feed may need a private pricing/stock join before the actual importer accepts it. Match existing products by external ID consistently, or original article when present.
