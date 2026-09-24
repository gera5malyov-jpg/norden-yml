#!/usr/bin/env python3
from pathlib import Path

path = Path('catalog/sync_norden_chairs_pipeline.py')
text = path.read_text(encoding='utf-8')
fn = text.index('def load_supplier():')
start = text.index('    target = {}\n', fn)
end = text.index('    if len(target) < MIN_TARGET:', start)
replacement = '''    if len(price_index) < MIN_PRICE_ROWS:\n        raise RuntimeError(f"Safety stop: Norden price feed too small: {len(price_index)}")\n\n    def build_target(source):\n        target = {}\n        target_key_owner = {}\n        target_conflicts = []\n        for article, raw in source.items():\n            if not category_is_chair_stool(raw):\n                continue\n            item = dict(raw)\n            key = supplier_key(article)\n            p = price_index.get(key)\n            if p:\n                item["purchase"] = p["purchase"]\n                item["rrp"] = p["rrp"]\n                item["stock_msk"] = p["msk"]\n                item["stock_spb"] = p["spb"]\n                item["stock_total"] = p["total"]\n            else:\n                item["purchase"] = None\n                item["rrp"] = None\n                item["stock_msk"] = 0\n                item["stock_spb"] = 0\n                item["stock_total"] = 0\n            item["article"] = article\n            if key in target_key_owner and target_key_owner[key] != article:\n                target_conflicts.append([target_key_owner[key], article])\n                continue\n            target_key_owner[key] = article\n            target[key] = item\n        return target, target_conflicts\n\n    target, target_conflicts = build_target(full)\n    category_fallback = False\n    # The Norden API can return product data while category resolution is unavailable.\n    # In that case use the complete Norden.xml for category/content identity, while\n    # prices and per-city stocks still come from the current price XML above.\n    if len(target) < MIN_TARGET:\n        xml_full, xml_duplicates = norden.source_from_xml(short=False)\n        if len(xml_full) < MIN_FULL_SOURCE:\n            raise RuntimeError(f"Safety stop: Norden XML full catalog too small: {len(xml_full)}")\n        xml_target, xml_conflicts = build_target(xml_full)\n        if len(xml_target) >= MIN_TARGET:\n            full = xml_full\n            duplicates = xml_duplicates\n            target = xml_target\n            target_conflicts = xml_conflicts\n            source_kind = f"xml-full-category-fallback+{source_kind}"\n            category_fallback = True\n\n'''
text = text[:start] + replacement + text[end:]
needle = '        "api_error": api_error,\n    }\n'
if needle not in text[fn:]:
    raise RuntimeError('load_supplier metadata return marker not found')
text = text[:fn] + text[fn:].replace(
    needle,
    '        "api_error": api_error,\n        "category_xml_fallback": category_fallback,\n    }\n',
    1,
)
path.write_text(text, encoding='utf-8')
