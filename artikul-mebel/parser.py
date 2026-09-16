from __future__ import annotations

import re
import sys

from bs4 import BeautifulSoup

import parser_core as _core


_original_parse_product = _core.parse_product


def _retail_price_from_actual_block(page_html: str) -> float | None:
    """Read the visible retail price from Bitrix #actual_price.

    Some Artikul-Mebel cards show the retail price as just ``10 515 ₽ / шт``
    without the words ``Розничная стоимость``.  The core parser historically
    fell back to the product-detail container in that case, where the price is
    absent, and emitted 0.
    """
    soup = BeautifulSoup(page_html, 'html.parser')
    price_node = soup.select_one('#actual_price')
    if not price_node:
        return None

    price_text = _core.clean_text(price_node.get_text(' ', strip=True))
    if not price_text:
        return None

    labelled = re.search(
        r'Розничная\s+(?:стоимость|цена)\s*([0-9\s\xa0]+(?:[.,][0-9]+)?)\s*₽',
        price_text,
        flags=re.I,
    )
    if labelled:
        return _core.money_to_float(labelled.group(1))

    # Wholesale prices on the site are introduced by "при заказе от".
    # Restrict the fallback to the portion before that marker so a wholesale
    # price can never become the retail price.
    wholesale_marker = re.search(r'при\s+заказе\s+от', price_text, flags=re.I)
    retail_scope = price_text[:wholesale_marker.start()] if wholesale_marker else price_text
    first_price = re.search(
        r'([0-9][0-9\s\xa0]*(?:[.,][0-9]+)?)\s*₽',
        retail_scope,
    )
    if not first_price:
        return None
    return _core.money_to_float(first_price.group(1))


def parse_product(page_html: str, url: str, category_hint: str | None = None) -> dict:
    product = _original_parse_product(page_html, url, category_hint)
    explicit_price = _retail_price_from_actual_block(page_html)
    if explicit_price is not None and explicit_price > 0:
        product['price'] = float(explicit_price)
    return product


# load_one/main are defined in parser_core, so patch its global parse_product.
# When imported as `parser`, expose the core module itself; this preserves
# monkeypatching behaviour in the existing tests (parser.fetch, etc.).
_core.parse_product = parse_product

if __name__ == '__main__':
    _core.main()
else:
    sys.modules[__name__] = _core
