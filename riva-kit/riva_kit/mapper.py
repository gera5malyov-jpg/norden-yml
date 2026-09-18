def _unique(values):
    out = []
    seen = set()
    for value in values:
        text = str(value or '').strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _skip_param(title):
    low = str(title or '').strip().casefold()
    if not low:
        return True
    if low in {
        'похожие товары по фильтру',
        'для продажи (готовое изделие)',
        'в наличии',
        '2 склад центральный рязань',
    }:
        return True
    if 'количество на складе' in low:
        return True
    if 'склад' in low and 'количество' in low:
        return True
    if low.startswith('прайс') or low.startswith('ррц:'):
        return True
    return False


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
        if not _skip_param(title):
            add(title, values)

    if offer.barcode:
        add('Штрихкод', [offer.barcode])
    if offer.weight:
        add('Вес', [offer.weight])
    add('ID предложения Riva', [offer.source_id])
    if offer.group_id:
        add('ID группы Riva', [offer.group_id])
    return result
