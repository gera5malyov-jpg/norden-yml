from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

MONEY = Decimal('0.01')


def to_kit_sku(source_id):
    value = str(source_id or '').strip()
    if not value:
        raise ValueError('Riva offer id is missing')
    return 'riva-' + value


def normalize_price(value):
    try:
        price = Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if price <= 0:
        return None
    return price.quantize(MONEY, rounding=ROUND_HALF_UP)


def parse_count(value):
    try:
        count = int(Decimal(str(value).replace(',', '.')))
    except (InvalidOperation, ValueError, TypeError):
        return 0
    return max(0, count)


def desired_stock(count, mode='binary100'):
    count = max(0, int(count or 0))
    if mode == 'actual':
        return count
    if mode != 'binary100':
        raise ValueError(f'Unsupported stock mode: {mode}')
    return 100 if count > 0 else 0
