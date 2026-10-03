from .models import ValidationReport

def _pct(old,new):
    if old in (None,0,"","0") or new is None: return None
    try: return abs(float(new)-float(old))/abs(float(old))*100.0
    except (TypeError,ValueError,ZeroDivisionError): return None

def validate_run(products, previous_count=None, max_price_change_pct=100.0, min_count_ratio=0.60, previous_by_sku=None):
    r=ValidationReport(total=len(products))
    seen=set()
    previous_by_sku=previous_by_sku or {}
    for p in products:
        if not p.supplier_sku: r.errors.append("Пустой артикул поставщика")
        if not p.sku: r.errors.append(f"Пустой итоговый артикул: {p.supplier_sku}")
        if p.sku in seen: r.errors.append(f"Дубль артикула: {p.sku}")
        seen.add(p.sku)
        if p.stock is not None and p.stock < 0: r.errors.append(f"Отрицательный остаток: {p.sku}")
        for label,value in (("закупка",p.purchase_price),("цена",p.price),("цена до скидки",p.compare_price)):
            if value is not None and value < 0: r.errors.append(f"Отрицательная {label}: {p.sku}")
        old=previous_by_sku.get(p.sku,{})
        for field in ("purchase_price","price","compare_price"):
            pct=_pct(old.get(field),getattr(p,field))
            if pct is not None and pct>max_price_change_pct:
                r.errors.append(f"Аномальное изменение {field}: {p.sku} ({pct:.1f}%)")
    if previous_count and len(products) < previous_count * min_count_ratio:
        r.errors.append(f"Источник вернул слишком мало товаров: {len(products)} вместо ~{previous_count}")
    r.blocked=bool(r.errors)
    return r
