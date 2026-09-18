from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

MONEY = Decimal('0.01')


def to_kit_sku(vendor_code):
    code = str(vendor_code or '').strip()
    if not code:
        raise ValueError('Liga vendorCode is missing')
    return 'liga-' + code


def normalize_price(value):
    try:
        price = Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if price <= 0:
        return None
    return price.quantize(MONEY, rounding=ROUND_HALF_UP)


def desired_stock(available):
    return 100 if bool(available) else 0
