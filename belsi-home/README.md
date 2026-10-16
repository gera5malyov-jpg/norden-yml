# BELSI HOME - YML Pro

Source: https://belsi-home.ru/ and current sitemap-iblock-2.xml. Read **all** product categories. Each distinct Bitrix OFFERS item becomes its own product card, including each color, finish, and orientation.

Every product variant:
* Immutable YML offer id `belsi-<Bitrix offer ID>`
* `vendorCode` from the variant's actual article, fallback BELSI-<ID>
* Variant-specific name, price, availability and photos
* Original full-size images from /upload/iblock/... instead of /upload/resize_cache/..., eliminating resize-added watermark. A watermark baked into the original image cannot be automatically guaranteed absent.
* Colors and extra options as parameters
* Product-only description with supplier contact data and marketing removed
* Stable group_id by model for related variants, while YML offer remains independent.

Stable URL for YML Pro after the first successful generation:

https://raw.githubusercontent.com/gera5malyov-jpg/norden-yml/main/belsi-home/catalog.yml

GitHub Actions: every Sunday at 05:47 UTC (08:47 Moscow), and manual dispatch. Generator fails closed if there are missing variants, malformed images, network errors, or a sharp shrink from the prior YML; existing published file remains unchanged. It does not upload products to KIT or Webasyst: configure YML Pro independently to pull this feed.