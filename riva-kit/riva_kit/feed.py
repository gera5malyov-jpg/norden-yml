import xml.etree.ElementTree as ET

from .model import RivaCategory, RivaOffer
from .rules import normalize_price, parse_count, to_kit_sku


def _tag(elem):
    return elem.tag.split('}')[-1]


def _text(node, tag, default=''):
    for child in list(node):
        if _tag(child) == tag:
            return str(child.text or '').strip()
    return default


def _params(offer):
    grouped = {}
    for node in list(offer):
        if _tag(node) != 'param':
            continue
        name = str(node.attrib.get('name') or '').strip()
        value = str(node.text or '').strip()
        if not name or not value:
            continue
        values = grouped.setdefault(name, [])
        if value not in values:
            values.append(value)
    return grouped


def _param_first(params, wanted_title):
    wanted = str(wanted_title or '').strip().casefold()
    for title, values in params.items():
        if str(title or '').strip().casefold() != wanted:
            continue
        for value in values:
            text = str(value or '').strip()
            if text:
                return text
    return ''


def _images(offer):
    out = []
    seen = set()
    for node in list(offer):
        if _tag(node) != 'picture':
            continue
        url = str(node.text or '').strip()
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def parse_categories(path):
    categories = {}
    inside_categories = False
    for event, elem in ET.iterparse(path, events=('start', 'end')):
        tag = _tag(elem)
        if event == 'start' and tag == 'categories':
            inside_categories = True
            continue
        if event == 'end' and tag == 'categories':
            inside_categories = False
            elem.clear()
            break
        if event == 'end' and inside_categories and tag == 'category':
            source_id = str(elem.attrib.get('id') or '').strip()
            name = str(elem.text or '').strip()
            if source_id and name:
                parent_id = str(elem.attrib.get('parentId') or '').strip() or None
                categories[source_id] = RivaCategory(source_id, name, parent_id)
            elem.clear()
    return categories


def offer_from_element(node):
    source_id = str(node.attrib.get('id') or '').strip()
    if not source_id:
        raise ValueError('Riva offer id is missing')

    params = _params(node)
    article = _param_first(params, 'Артикул')
    if not article:
        raise ValueError(f'Riva article is missing for offer {source_id}')

    site_code = _param_first(params, 'Код для сайта')
    if not site_code:
        raise ValueError(f'Riva Код для сайта is missing for offer {source_id}')

    count = parse_count(_text(node, 'count', '0'))
    return RivaOffer(
        source_id=source_id,
        group_id=str(node.attrib.get('group_id') or '').strip(),
        article=article,
        kit_sku=to_kit_sku(site_code),
        count=count,
        in_stock=count > 0,
        category_id=_text(node, 'categoryId') or None,
        name=_text(node, 'name', article) or article,
        description=_text(node, 'description'),
        price=normalize_price(_text(node, 'price')),
        currency=_text(node, 'currencyId', 'RUR') or 'RUR',
        barcode=_text(node, 'barcode'),
        weight=_text(node, 'weight'),
        source_url=_text(node, 'url'),
        images=_images(node),
        params=params,
    )


def iter_offers(path, on_skip=None):
    for event, elem in ET.iterparse(path, events=('end',)):
        if _tag(elem) != 'offer':
            continue
        try:
            try:
                offer = offer_from_element(elem)
            except ValueError as exc:
                message = str(exc)
                if 'Код для сайта is missing' in message:
                    if on_skip is not None:
                        on_skip(message)
                    continue
                raise
            yield offer
        finally:
            elem.clear()
