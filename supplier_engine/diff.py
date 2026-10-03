def price_change_pct(old, new):
    if old in (None,0,"","0") or new is None: return None
    try: return abs(float(new)-float(old))/abs(float(old))*100.0
    except (TypeError,ValueError,ZeroDivisionError): return None

def validate_price_changes(plan, max_pct):
    errors=[]
    for row in plan.get("update",[]):
        desired=row.get("desired",{})
        current=row.get("current",{})
        for field in ("price","compare_price","purchase_price"):
            pct=price_change_pct(current.get(field),desired.get(field))
            if pct is not None and pct>max_pct:
                errors.append(f"{row.get('sku')}: {field} changes by {pct:.1f}%")
    return errors
