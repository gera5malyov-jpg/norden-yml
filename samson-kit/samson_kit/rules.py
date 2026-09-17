from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

MONEY = Decimal('0.01')

@dataclass(frozen=True)
class PriceDecision:
    sale: Decimal
    old: Decimal
    minimum: Decimal

def to_kit_sku(samson_code):
    code = str(samson_code).strip()
    if not code: raise ValueError('empty Samson code')
    return 'SAMS-' + code

def _money(value):
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)

def calculate_prices(purchase):
    try: purchase = Decimal(str(purchase))
    except (InvalidOperation, ValueError, TypeError): return None
    if purchase <= 0: return None
    sale_factor = Decimal('1.40') if purchase <= Decimal('3000.00') else Decimal('1.26')
    return PriceDecision(sale=_money(purchase * sale_factor), old=_money(purchase * Decimal('1.80')), minimum=_money(purchase * Decimal('1.20')))

def calculate_stock(parts, *, active, withdrawn):
    if withdrawn: return 0
    if not active or parts is None: return None
    try: values = [int(v) for v in parts]
    except (TypeError, ValueError): return None
    if any(v < 0 for v in values): return None
    total = sum(values)
    return total if total > 0 else 100
