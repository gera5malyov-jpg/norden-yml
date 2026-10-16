# Finsoffa -> Webasyst YML Pro

Public source: https://finsoffa.ru
Source discovery uses both sitemap.xml and every page of /catalog/?page=N to avoid losing variants.

Product colors and fabrics already have individual /product/... URLs, and their exact original article numbers are used as stable offer IDs (\`finsoffa-<article>\`). Photos are direct links to original /photos/upload/... URLs. Characteristics are taken from the dedicated characteristics tab. Full promotional vendor descriptions, advertising copy, company details, contacts, online store/checkout information are NOT imported.

Stock: available="true" only if the product page explicitly reports in stock, available="false" for preorder / made to order / unavailable. Also exported as product param name="Наличие". This is binary public availability, NOT actual stock units.

NO public retail prices, procurement prices, or numeric stock quantities exported. This is a content+availability feed. YML Pro may require mandatory prices. Configure imports carefully; if it cannot accept price-less feeds, combine this with a trustworthy price list before enabling automatic import.

Images: untouched originals with no resize, remote links. Original watermarks are not removed or edited; a clean authorized image source would be needed.

Failure safeguards: URL completeness, duplicate articles, every product parsed, photos required, sitemap size, no unexpected >20% shrink, atomic publication only on complete success.

Automated weekly Sunday refresh in GitHub Actions. No existing KIT or other suppliers changed.

Permanent URL after the first successful workflow:
https://raw.githubusercontent.com/gera5malyov-jpg/norden-yml/main/finsoffa/catalog-content.yml
