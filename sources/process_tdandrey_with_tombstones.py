from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone

import process_supplier_deltas as generic
import process_tdandrey as td

TOMBSTONE_DAYS = 7


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def main() -> int:
    products, response_format = td.fetch_all()

    offers: dict[str, bytes] = {}
    categories: dict[str, str] = {}
    stock_paths: set[str] = set()
    all_api_keys: set[str] = set()
    filtered = 0
    duplicates: list[str] = []

    for index, product in enumerate(products):
        flat = td.flatten(product)
        original_key = td.identity(flat, index)
        all_api_keys.add(original_key)

        category_id = td.first_value(flat, td.CATEGORY_ID_KEYS)
        category_name = td.first_value(flat, td.CATEGORY_NAME_KEYS)
        if category_id:
            categories[category_id] = category_name or category_id

        built = td.build_offer(product, index)
        if built is None:
            filtered += 1
            continue

        key, raw, category_id, category_name, paths = built
        stock_paths.update(paths)
        if key in offers:
            duplicates.append(key)
            key = f"{key}__duplicate__{index}"
            raw = raw.replace(
                b"<offer id=",
                f"<offer id={td.quoteattr(key)} data-original-id=".encode("utf-8"),
                1,
            )
        offers[key] = raw
        if category_id:
            categories[category_id] = category_name or category_id

    hashes = {
        key: hashlib.sha256(re.sub(rb">\s+<", b"><", raw.strip())).hexdigest()
        for key, raw in offers.items()
    }

    previous_state = td.load_state()
    previous_hashes = previous_state.get("offers", {})
    previous_tombstones = previous_state.get("tombstones", {})

    changed = [
        key for key, digest in hashes.items()
        if previous_hashes.get(key) != digest
    ]
    removed = [key for key in previous_hashes if key not in hashes]

    now = datetime.now(timezone.utc)
    active_tombstones: dict[str, dict[str, str]] = {}

    # Уже обнулённые/пропавшие товары повторяем при каждом запуске в течение 7 суток.
    for key, tombstone in previous_tombstones.items():
        if key in hashes:
            # Товар снова появился в наличии — нулевую запись больше не передаём.
            continue

        try:
            first_missing = parse_utc(tombstone["first_missing_at_utc"])
            zeroed_offer = base64.b64decode(tombstone["offer_b64"])
        except Exception:
            continue

        if now - first_missing < timedelta(days=TOMBSTONE_DAYS):
            active_tombstones[key] = {
                "first_missing_at_utc": first_missing.isoformat(timespec="seconds"),
                "offer_b64": base64.b64encode(zeroed_offer).decode("ascii"),
            }

    # Новый переход в 0 или исчезновение из API: восстанавливаем последнюю реально
    # переданную версию offer из Git-истории и обнуляем в ней все поля остатков.
    new_removed = [key for key in removed if key not in previous_tombstones]
    recovered = generic.recover_offers_from_git("tdandrey", new_removed)

    unrecovered: list[str] = []
    for key in new_removed:
        raw = recovered.get(key)
        if raw is None:
            unrecovered.append(key)
            continue

        zeroed = generic.zero_stock_offer(raw)
        active_tombstones[key] = {
            "first_missing_at_utc": now.isoformat(timespec="seconds"),
            "offer_b64": base64.b64encode(zeroed).decode("ascii"),
        }

    # changed/tdandrey.xml содержит:
    # 1) новые/изменённые товары с положительным остатком хотя бы на одном складе;
    # 2) товары, ставшие 0 или исчезнувшие из API, с нулевыми остатками — 7 суток подряд.
    output_offers = [offers[key] for key in changed]
    output_offers.extend(
        base64.b64decode(tombstone["offer_b64"])
        for tombstone in active_tombstones.values()
    )

    body = b"\n" + b"\n".join(output_offers) + b"\n" if output_offers else b"\n"
    yml = td.prefix(categories) + body + b"</offers>\n  </shop>\n</yml_catalog>\n"
    output_changed = td.write_if_changed(td.OUTPUT_PATH, yml)

    disappeared_from_api = sorted(key for key in removed if key not in all_api_keys)
    zero_on_all_warehouses = sorted(key for key in removed if key in all_api_keys)

    state = {
        "source_url": td.SOURCE_URL,
        "checked_at_utc": now.isoformat(timespec="seconds"),
        "api_format": response_format,
        "api_product_count": len(products),
        "in_stock_any_warehouse_offer_count": len(offers),
        "out_of_stock_all_warehouses_filtered_count": filtered,
        "changed_count": len(changed),
        "removed_from_in_stock_count": len(removed),
        "removed_from_in_stock_offer_keys": removed,
        "disappeared_from_api_count": len(disappeared_from_api),
        "disappeared_from_api_offer_keys": disappeared_from_api,
        "zero_on_all_warehouses_count": len(zero_on_all_warehouses),
        "zero_on_all_warehouses_offer_keys": zero_on_all_warehouses,
        "zero_stock_retention_days": TOMBSTONE_DAYS,
        "zero_stock_count": len(active_tombstones),
        "zero_stock_offer_keys": sorted(active_tombstones),
        "unrecovered_removed_offer_keys": unrecovered,
        "duplicate_offer_keys": duplicates,
        "detected_stock_field_paths": sorted(stock_paths),
        "warehouses": [
            {"code": code, "label": label, "name": full_name}
            for code, label, full_name in td.WAREHOUSES
        ],
        "tombstones": active_tombstones,
        "offers": hashes,
    }
    state_bytes = (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    state_changed = td.write_if_changed(td.STATE_PATH, state_bytes)

    if unrecovered:
        print(
            "[tdandrey] ПРЕДУПРЕЖДЕНИЕ: не удалось восстановить для обнуления: "
            + ", ".join(unrecovered),
            file=sys.stderr,
        )

    print(
        f"[tdandrey] API={response_format}; всего={len(products)}; "
        f"в_наличии={len(offers)}; без_наличия={filtered}; изменено={len(changed)}; "
        f"новых_обнулений={len(new_removed)}; активных_нулей_7д={len(active_tombstones)}; "
        f"пропало_из_API={len(disappeared_from_api)}; стало_0_на_всех_складах={len(zero_on_all_warehouses)}; "
        f"файл_изменен={output_changed}; состояние_изменено={state_changed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
