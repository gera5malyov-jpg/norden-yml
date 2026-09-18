def _unique(values):
    out = []
    seen = set()
    for value in values:
        text = str(value or '').strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def characteristics_from_offer(offer):
    result = []
    seen_titles = set()

    def add(title, values):
        title = str(title or '').strip()
        vals = _unique(values if isinstance(values, (list, tuple)) else [values])
        key = title.casefold()
        if not title or not vals or key in seen_titles:
            return
        seen_titles.add(key)
        result.append((title, vals))

    for title, values in offer.params.items():
        add(title, values)

    if offer.barcode:
        add('Штрихкод', [offer.barcode])
    if offer.country_of_origin:
        add('Страна производства', [offer.country_of_origin])
    if offer.manufacturer_warranty:
        add('Гарантия производителя', ['Да'])
    if offer.weight:
        add('Вес', [offer.weight])
    if offer.dimensions:
        add('Габариты', [offer.dimensions])
    add('Артикул Liga', [offer.vendor_code])
    return result
