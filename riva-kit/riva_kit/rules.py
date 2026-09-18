from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

MONEY = Decimal('0.01')
SALE_MULTIPLIER = Decimal('1.26')
OLD_MULTIPLIER = Decimal('1.80')
MINIMUM_MULTIPLIER = Decimal('1.20')


def to_kit_sku(site_code):
    value = str(site_code or '').strip()
    if not value:
        raise ValueError('Riva Код для сайта is missing')
    return value


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


def calculate_prices(purchase_price):
    purchase = normalize_price(purchase_price)
    if purchase is None:
        return None
    return {
        'purchase': purchase,
        'sale': (purchase * SALE_MULTIPLIER).quantize(MONEY, rounding=ROUND_HALF_UP),
        'old': (purchase * OLD_MULTIPLIER).quantize(MONEY, rounding=ROUND_HALF_UP),
        'minimum': (purchase * MINIMUM_MULTIPLIER).quantize(MONEY, rounding=ROUND_HALF_UP),
    }


def desired_stock(count):
    count = max(0, int(count or 0))
    return count if count > 0 else 100
