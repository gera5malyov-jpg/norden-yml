from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import process_tdandrey as td

BASE_DIR = Path(__file__).resolve().parent
FULL_PATH = BASE_DIR / "full" / "tdandrey_initial.xml"
BASELINE_PATH = BASE_DIR / "state" / "tdandrey_initial.json"


def write_if_changed(path: Path, content: bytes) -> bool:
    if path.exists() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return True


def main() -> int:
    # Это одноразовый стартовый снимок для первичной загрузки на сайт.
    # После первого успешного создания не перезаписываем его, чтобы база
    # сравнения точно соответствовала тому файлу, который загрузил пользователь.
    if FULL_PATH.exists() and BASELINE_PATH.exists():
        print("[tdandrey-initial] стартовый полный снимок уже создан; пропуск")
        return 0

    products, response_format = td.fetch_all()
    offers: dict[str, bytes] = {}
    categories: dict[str, str] = {}
    duplicates: list[str] = []

    for index, product in enumerate(products):
        built = td.build_offer(product, index)
        if built is None:
            continue

        key, raw, category_id, category_name, _paths = built
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

    body = b"\n" + b"\n".join(offers.values()) + b"\n" if offers else b"\n"
    yml = td.prefix(categories) + body + b"</offers>\n  </shop>\n</yml_catalog>\n"
    write_if_changed(FULL_PATH, yml)

    baseline = {
        "snapshot_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_url": td.SOURCE_URL,
        "api_format": response_format,
        "api_product_count": len(products),
        "in_stock_any_warehouse_offer_count": len(offers),
        "duplicate_offer_keys": duplicates,
        "warehouses": [
            {"code": code, "label": label, "name": full_name}
            for code, label, full_name in td.WAREHOUSES
        ],
        "offers": hashes,
    }
    baseline_bytes = (json.dumps(baseline, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    write_if_changed(BASELINE_PATH, baseline_bytes)

    print(
        f"[tdandrey-initial] API={response_format}; всего={len(products)}; "
        f"в полном стартовом YML={len(offers)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
