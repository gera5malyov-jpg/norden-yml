# Champion Tool - Webasyst YML Pro

Website: https://championtool.ru/
Feed (available after successful complete first run):
https://raw.githubusercontent.com/gera5malyov-jpg/norden-yml/main/championtool/catalog-content.yml

The full public sitemap contains approximately 30,000 URLs, including product categories and informational pages. The parser downloads product cards and excludes category/news pages, then merges twelve validated sharded exports.

Product fields: stable URL-derived offer ID, original article in vendorCode, product model and name, all valid original image links under /media/images/product, factual description compiled from specification fields, product hierarchy, and Russian stock label. Site contacts, delivery/payment advertisements, supplier promotion and contact details are not exported. The true product brand in product titles remains.

Availability: available=true only when a functional buy form and product-level InStock schema both confirm purchasability. Explicit preorder / out of stock is available=false. Ambiguous status is not presumed in stock and is emitted without an available attribute; param "Наличие" marks it "Не подтверждено". There is no numeric inventory claim.

Prices, purchase costs and integer warehouse quantities are never exported; this content+availability YML is NOT a complete sales feed until price data is joined. In YML Pro, disable price and numeric-stock import and carefully map availability; if price is mandatory, first merge an authorized price list rather than assuming the public retail price is a purchase price.

Schedule weekly on Sunday. GitHub Actions matrix shards the full catalog, safeguards against excessive fetch failures, requires >=15,000 offers, checks mass loss versus previous published feed, and only then pushes the final file. Existing KIT/Webasyst imports and all other suppliers remain unchanged.

Watermarks: the parser selects original images without resizing. It does not remove watermarks baked into the image pixels. A supplier-authorized watermark-free source would be needed.

Because the first full run may take considerable time and generate a large file, check GitHub Actions for the final published status before pasting the URL in YML Pro.
