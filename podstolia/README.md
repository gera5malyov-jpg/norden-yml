# PODSTOLIA.RU enriched feed

Source feed: https://podstolia.ru/modules/shop/shop.all.php

This integration preserves the supplier offer content and order and adds only:

```xml
<purchase_price>...</purchase_price>
```

immediately after the existing `<price>` tag when the supplier article has a numeric dealer price in the supplied price list dated 2026-07-22.

Rules:
- matching key: `vendorcode` / supplier article;
- numeric dealer price is used as `purchase_price`;
- if the price list says `ожидается`, no purchase price is invented;
- if an article is absent from the price list, the offer is left unchanged;
- source feed is refreshed once a week: Monday at 10:00 Moscow time;
- the workflow commits only when the generated feed actually changes.

Stable raw feed:
https://raw.githubusercontent.com/gera5malyov-jpg/norden-yml/main/podstolia/podstolia.yml
