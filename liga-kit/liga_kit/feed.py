import xml.etree.ElementTree as ET

from .model import FeedSnapshot, LigaCategory, LigaOffer
from .rules import normalize_price, to_kit_sku


def _text(node, tag, default=''):
    child = node.find(tag)
    if child is None or child.text is None:
        return default
    return str(child.text).strip()


def _boolish(value):
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'y', 'да'}


def _split_images(offer):
    out = []
    seen = set()
    for node in offer.findall('picture'):
        for part in str(node.text or '').split(','):
            url = part.strip()
            if url and url not in seen:
                seen.add(url)
                out.append(url)
    return out


def _params(offer):
    grouped = {}
    for node in offer.findall('param'):
        name = str(node.attrib.get('name') or '').strip()
        value = str(node.text or '').strip()
        if not name or not value:
            continue
        values = grouped.setdefault(name, [])
        if value not in values:
            values.append(value)
    return grouped


def _offer_from_element(node):
    vendor_code = _text(node, 'vendorCode')
    if not vendor_code:
        raise ValueError('Liga vendorCode is missing')
    category_id = _text(node, 'categoryId') or None
    return LigaOffer(
        source_id=str(node.attrib.get('id') or '').strip(),
        vendor_code=vendor_code,
        kit_sku=to_kit_sku(vendor_code),
        available=str(node.attrib.get('available', 'false')).strip().lower() == 'true',
        category_id=category_id,
        name=_text(node, 'name', vendor_code) or vendor_code,
        description=_text(node, 'description'),
        vendor=_text(node, 'vendor'),
        price=normalize_price(_text(node, 'price')),
        currency=_text(node, 'currencyId', 'RUB') or 'RUB',
        barcode=_text(node, 'barcode'),
        weight=_text(node, 'weight'),
        dimensions=_text(node, 'dimensions'),
        source_url=_text(node, 'url'),
        country_of_origin=_text(node, 'country_of_origin'),
        manufacturer_warranty=_boolish(_text(node, 'manufacturer_warranty')),
        images=_split_images(node),
        params=_params(node),
    )


def parse_feed(path):
    categories = {}
    offers = []
    stack = []
    for event, elem in ET.iterparse(path, events=('start', 'end')):
        if event == 'start':
            stack.append(elem.tag)
            continue

        if elem.tag == 'category' and 'categories' in stack:
            source_id = str(elem.attrib.get('id') or '').strip()
            name = str(elem.text or '').strip()
            if source_id and name:
                parent_id = str(elem.attrib.get('parentId') or '').strip() or None
                categories[source_id] = LigaCategory(source_id, name, parent_id)
            elem.clear()
        elif elem.tag == 'offer':
            offers.append(_offer_from_element(elem))
            elem.clear()

        if stack:
            stack.pop()

    return FeedSnapshot(categories=categories, offers=offers, complete=True)
