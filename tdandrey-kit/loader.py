#!/usr/bin/env python3
from pathlib import Path
import base64
import gzip

ROOT = Path(__file__).resolve().parent
payload = "".join(
    (ROOT / f"payload.{i:02d}").read_text(encoding="utf-8").strip()
    for i in range(1, 5)
)
source = gzip.decompress(base64.b64decode(payload)).decode("utf-8")

old_stocks = '''    stock_rows = listify(wa.call("shop.stock.getList"))
    stock_ids = [s(x.get("id")) for x in stock_rows if s(x.get("id"))]
'''
new_stocks = '''    stock_rows = listify(wa.call("shop.stock.getList"))
    stock_by_name = {
        norm(x.get("name") or x.get("title")): s(x.get("id"))
        for x in stock_rows if s(x.get("id"))
    }
    msk_stock_id = stock_by_name.get(norm("ТД Андрей МСК"))
    spb_stock_id = stock_by_name.get(norm("ТД Андрей СПБ"))
    if not msk_stock_id or not spb_stock_id:
        raise RuntimeError("В Webasyst не найдены склады 'ТД Андрей МСК' и/или 'ТД Андрей СПБ'")
    stock_ids = [msk_stock_id, spb_stock_id]
'''
if old_stocks not in source:
    raise SystemExit("Не найден блок складов Webasyst для безопасного патча")
source = source.replace(old_stocks, new_stocks, 1)

old_payload = '''        payload = {
            "purchase_price": str(prices["purchase"]),
            "price": str(prices["wa_sale"]),
            "compare_price": str(prices["compare"]),
        }
'''
new_payload = '''        msk_qty = as_int((((item.get("stock") or {}).get("msk") or {}).get("value")))
        spb_qty = as_int((((item.get("stock") or {}).get("spb") or {}).get("value")))
        payload = {
            "purchase_price": str(prices["purchase"]),
            "price": str(prices["wa_sale"]),
            "compare_price": str(prices["compare"]),
            "stock": {
                msk_stock_id: str(msk_qty),
                spb_stock_id: str(spb_qty),
            },
            "available": 1 if (msk_qty + spb_qty) > 0 else 0,
        }
'''
if old_payload not in source:
    raise SystemExit("Не найден блок цен Webasyst для безопасного патча")
source = source.replace(old_payload, new_payload, 1)

old_category = '''    if not usable:
        return [KIT_ROOT_CATEGORY]
    longest = max(usable, key=len)
    return [KIT_ROOT_CATEGORY] + longest
'''
new_category = '''    if not usable:
        return [KIT_ROOT_CATEGORY]
    longest = max(usable, key=len)
    return longest
'''
if old_category not in source:
    raise SystemExit("Не найден блок категорий ТД Андрей для безопасного патча")
source = source.replace(old_category, new_category, 1)

exec(
    compile(source, "sync_tdandrey.py", "exec"),
    {"__name__": "__main__", "__file__": str(ROOT / "sync_tdandrey.py")},
)
