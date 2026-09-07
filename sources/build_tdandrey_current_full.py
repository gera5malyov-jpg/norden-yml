from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import process_tdandrey as td

BASE_DIR = Path(__file__).resolve().parent
FULL_PATH = BASE_DIR / "full" / "tdandrey_current.xml"
META_PATH = BASE_DIR / "full" / "tdandrey_current_meta.json"


def write_if_changed(path: Path, content: bytes) -> bool:
    if path.exists() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return True


def main() -> int:
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

    body = b"\n" + b"\n".join(offers.values()) + b"\n" if offers else b"\n"
    yml = td.prefix(categories) + body + b"</offers>\n  </shop>\n</yml_catalog>\n"
    write_if_changed(FULL_PATH, yml)

    meta = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_url": td.SOURCE_URL,
        "api_format": response_format,
        "api_product_count": len(products),
        "in_stock_any_warehouse_offer_count": len(offers),
        "duplicate_offer_keys": duplicates,
        "warehouses": [
            {"code": code, "label": label, "name": full_name}
            for code, label, full_name in td.WAREHOUSES
        ],
    }
    meta_bytes = (json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    write_if_changed(META_PATH, meta_bytes)

    print(
        f"[tdandrey-current-full] API={response_format}; всего={len(products)}; "
        f"в наличии хотя бы на одном из 3 складов={len(offers)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
