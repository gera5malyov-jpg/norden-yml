import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from samson_kit.http import SafeSession
from samson_kit.samson_client import SamsonClient
from samson_kit.kit_client import KitClient
from samson_kit.mapper import normalize_sku

MIN_TITLE = 'Минимальный заказ'
STEP_TITLE = 'Шаг заказа'


def resolve_characteristic(kit, title):
    rows = [
        row for row in kit.list_characteristics()
        if str(row.get('title', '')).strip() == title
    ]
    if len(rows) > 1:
        raise RuntimeError(f'ambiguous KIT characteristic {title!r}: {len(rows)} matches')
    if rows:
        return str(rows[0].get('id', '')).strip()
    created = kit.create_characteristic(title, char_type='STRING', select_mode='SINGLE')
    characteristic_id = str(created.get('id', '')).strip()
    if not characteristic_id:
        raise RuntimeError(f'KIT did not return id for characteristic {title!r}')
    return characteristic_id


def merge_constraint_characteristics(current, min_id, step_id, minimum, step):
    kept = []
    for row in current or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get('characteristic_id', '')).strip()
        if cid in (min_id, step_id):
            continue
        kept.append(row)
    kept.append({
        'characteristic_id': min_id,
        'value': str(int(minimum)),
        'values': [str(int(minimum))],
    })
    kept.append({
        'characteristic_id': step_id,
        'value': str(int(step)),
        'values': [str(int(step))],
    })
    return kept


def main():
    http = SafeSession()
    samson = SamsonClient(os.environ['SAMSON_API_KEY'], http)
    kit = KitClient(os.environ['YANDEX_KIT_TOKEN'], http)

    index, duplicates = kit.index_samson_variants()
    if duplicates:
        raise RuntimeError(f'duplicate SAMS variants found: {len(duplicates)}')

    min_id = resolve_characteristic(kit, MIN_TITLE)
    step_id = resolve_characteristic(kit, STEP_TITLE)

    source_seen = 0
    constrained_source = 0
    existing_constrained = 0
    patched = 0
    verified = 0
    examples = []

    for raw in samson.iter_skus():
        item = normalize_sku(raw)
        source_seen += 1
        if item.min_order_quantity == 1 and item.order_step == 1:
            continue
        constrained_source += 1
        variant = index.get(item.kit_sku)
        if not variant:
            continue
        existing_constrained += 1
        variant_id = str(variant.get('id', '')).strip()
        detail = kit.get_variant(variant_id)
        current = detail.get('characteristics') or []

        current_values = {}
        for row in current:
            if not isinstance(row, dict):
                continue
            cid = str(row.get('characteristic_id', '')).strip()
            if cid in (min_id, step_id):
                current_values[cid] = str(row.get('value', '')).strip()

        wanted_min = str(item.min_order_quantity)
        wanted_step = str(item.order_step)
        if current_values.get(min_id) == wanted_min and current_values.get(step_id) == wanted_step:
            verified += 1
            continue

        merged = merge_constraint_characteristics(
            current,
            min_id,
            step_id,
            item.min_order_quantity,
            item.order_step,
        )
        kit.update_variant(variant_id, {'characteristics': merged})
        after = kit.get_variant(variant_id)
        after_values = {
            str(row.get('characteristic_id', '')).strip(): str(row.get('value', '')).strip()
            for row in (after.get('characteristics') or [])
            if isinstance(row, dict)
        }
        if after_values.get(min_id) != wanted_min or after_values.get(step_id) != wanted_step:
            raise RuntimeError(
                f'order-constraint verification failed for {item.kit_sku}: '
                f'{after_values.get(min_id)!r}/{after_values.get(step_id)!r} '
                f'expected {wanted_min!r}/{wanted_step!r}'
            )
        patched += 1
        verified += 1
        if len(examples) < 20:
            examples.append({
                'sku': item.kit_sku,
                'minimum': item.min_order_quantity,
                'step': item.order_step,
            })
        if patched and patched % 100 == 0:
            print(f'PROGRESS patched={patched} verified={verified}', flush=True)

    print('ORDER_CONSTRAINT_MIGRATION=' + json.dumps({
        'source_seen': source_seen,
        'constrained_source': constrained_source,
        'existing_constrained': existing_constrained,
        'patched': patched,
        'verified': verified,
        'examples': examples,
        'min_characteristic_id': min_id,
        'step_characteristic_id': step_id,
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
