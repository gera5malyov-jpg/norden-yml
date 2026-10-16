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



# Import-time extensions deliberately keep parser_core and its regression tests intact.
import os
from urllib.parse import urlparse, urlunparse
import xml.etree.ElementTree as ET

# Remove dealer contact details and supplier self-promotion from product descriptions.
CONTACT_RE = re.compile(
    r'(?i)(?:https?://|www\.|[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}|'
    r'(?:\+7|8[\s(]*800|8[\s(]*812)\s*[\d\s()\-]{8,}|'
    r'\b(?:телефон(?:ы)?|позвоните|звоните|свяжитесь|напишите|'
    r'адрес(?:у|а)?\s+(?:офиса|склада|производства|нашего|компании)|'
    r'офис продаж|отдел продаж|менеджер(?:у|а|ом)?|'
    r'оставьте заявку|оставить заявку|сделайте заказ|заказать звонок|'
    r'наш(?:ей|а|и|ем|у|е)?\s+(?:компани[июей]|организаци[июей]|сайте|магазине|'
    r'производств[ео]|предприяти[ея]|менеджер[уаом]?|специалист[ауом]?|отдел[ауом]?)|'
    r'ООО\s*[«"]?\s*Артикул[\s-]*Мебель|'
    r'специальное предложение для дилеров|оптовые цены по запросу|'
    r'закажите\s+(?:у\s+нас|на\s+сайте)|купить\s+(?:у\s+нас|в\s+нашем)|'
    r'адрес\s*:\s*|тел\.\s*[:+0-9]))'
)
SUPPLIER_TERMS_RE = re.compile(
    r'(?i)\b(?:Артикул[\s-]*Мебель|artikul[\s-]*mebel\.ru)\b'
)
_ORIGINAL_COMPOSE = _core.compose_description
_ORIGINAL_PICTURES = _core.extract_pictures
_ORIGINAL_BUILD = _core.build_yml

def sanitize_description(value: str) -> str:
    """Keep factual furniture text, discard provider contacts and promotional sentences."""
    if not value:
        return ''
    text = _core.clean_text(str(value).replace('\r', '\n'))
    # Split at normal sentence boundaries; avoid splitting decimal measurements.
    sentences = re.split(r'(?<=[.!?])\s+(?=[А-ЯЁA-ZА-Яа-яЁё])|[\n]+', text)
    kept = []
    for sentence in sentences:
        if CONTACT_RE.search(sentence):
            continue
        sentence = SUPPLIER_TERMS_RE.sub('', sentence)
        sentence = _core.clean_text(sentence)
        if sentence and sentence not in kept:
            kept.append(sentence)
    return ' '.join(kept).strip()[:12000]

def clean_params(params):
    # Never leak a hidden contact field into characteristics or description.
    filtered = {}
    for key, value in (params or {}).items():
        k = _core.clean_text(str(key))
        v = _core.clean_text(str(value))
        if not k or not v or CONTACT_RE.search(k) or CONTACT_RE.search(v):
            continue
        filtered[k] = v
    return filtered

def compose_description(base_description, params, additional_text, *, include_characteristics):
    base = sanitize_description(base_description)
    more = sanitize_description(additional_text)
    clean = clean_params(params)
    result = _ORIGINAL_COMPOSE(base, clean, more, include_characteristics=include_characteristics)
    return sanitize_description(result)

def extract_pictures(soup, container):
    """Prefer actual unresized supplier image files to resized branded overlays."""
    source = _ORIGINAL_PICTURES(soup, container)
    out = []
    for url in source:
        parsed = urlparse(url)
        path = re.sub(r'^/upload/resize_cache/(iblock|uf)/([^/]+)/[^/]+/(.+)$',
                      r'/upload/\1/\2/\3', parsed.path)
        # Bitrix has a real file at /upload/iblock/... backing a resize_cache URL.
        original = urlunparse(parsed._replace(path=path, query=''))
        if original not in out:
            out.append(original)
    return out

def parse_product(page_html: str, url: str, category_hint: str | None = None) -> dict:
    product = _original_parse_product(page_html, url, category_hint)
    explicit_price = _retail_price_from_actual_block(page_html)
    if explicit_price is not None and explicit_price > 0:
        product['price'] = float(explicit_price)
    product['params'] = clean_params(product.get('params'))
    product['_base_description'] = sanitize_description(product.get('_base_description',''))
    product['_additional_characteristics'] = sanitize_description(product.get('_additional_characteristics',''))
    product['description'] = sanitize_description(product.get('description',''))
    return product

def build_yml(products):
    products = list(products)
    if not products:
        raise RuntimeError('No products to publish')
    for product in products:
        product['description'] = sanitize_description(product.get('description', ''))
        product['params'] = clean_params(product.get('params'))
    payload = _ORIGINAL_BUILD(products)
    output = os.getenv('OUTPUT', 'catalog.yml')
    # Existing feed remains unchanged if the source becomes incomplete.
    if os.path.isfile(output) and os.getenv('ARTIKUL_ALLOW_CATALOG_SHRINK') != '1':
        previous = ET.parse(output)
        previous_ids = {x.get('id') for x in previous.findall('./shop/offers/offer')}
        new_ids = {x.get('id') for x in ET.fromstring(payload).findall('./shop/offers/offer')}
        missing = previous_ids - new_ids
        if previous_ids and (
            len(new_ids) < len(previous_ids) * 0.85
            or len(missing) > max(20, round(len(previous_ids) * 0.15))
        ):
            raise RuntimeError(
                f'Suspicious catalog loss: {len(missing)} of {len(previous_ids)} old SKUs missing; '
                'old YML preserved'
            )
    return payload

def load_one(url: str) -> list[dict]:
    """Fail the entire publication if a product or a listed modification is missing."""
    page = _core.fetch(url)
    refs = _core.extract_variant_refs(page, url)
    if not refs:
        return [_core.parse_product(page, url)]
    products = []
    for ref in refs:
        # Never skip failed variants: silently omitting them would shrink the feed.
        variant_html = _core.fetch(ref['url'])
        product = _core.parse_product(variant_html, ref['url'])
        product['name'] = ref['name'] or product['name']
        product['sku'] = ref['sku'] or product['sku']
        params = product.setdefault('params', {})
        params.update(clean_params(ref.get('chars')))
        product['description'] = _core.compose_description(
            product.get('_base_description', product.get('description','')),
            params, product.get('_additional_characteristics',''),
            include_characteristics=bool(ref.get('chars') or product.get('_additional_characteristics')),
        )
        params['Модификация'] = ref['variant']
        products.append(product)
    if len(products) != len(refs):
        raise RuntimeError(f'Incomplete variants for {url}: {len(products)}/{len(refs)}')
    return products

_core.parse_product = parse_product
_core.extract_pictures = extract_pictures
_core.compose_description = compose_description
_core.build_yml = build_yml
_core.load_one = load_one

if __name__ == '__main__':
    _core.main()
else:
    sys.modules[__name__] = _core
