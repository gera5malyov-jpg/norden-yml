#!/usr/bin/env python3
import json
import os
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

ROOT = Path(__file__).resolve().parents[1]
PRICE_FILE = ROOT / "kit-backup" / "raduga_purchase_prices.json"
REGISTRY_FILE = ROOT / "kit-backup" / "raduga_import_registry.json"
OUT = ROOT / "runtime"
OUT.mkdir(exist_ok=True)
REPORT_FILE = OUT / "raduga_purchase_price_sync.json"

SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1xZUeaWUr-6O3KK0MO-1U7IGBKXXfgcmZ-EJ-w-2wWug")

# Старые AF-SKU, которые отсутствуют в текущем KIT либо уже заняты другим товаром.
# Их нельзя использовать как автоматическую привязку.
UNSAFE_EXPECTED_SKUS = {
    "AF-31651675",
    "AF-31651828",
    "AF-31651835",
    "AF-31651849", "AF-31651850", "AF-31651851", "AF-31651852", "AF-31651853", "AF-31651854",
    "AF-31651872", "AF-31651873", "AF-31651874", "AF-31651875", "AF-31651876",
    "AF-31651935", "AF-31651936", "AF-31651937", "AF-31651938", "AF-31651939",
    "AF-31651940", "AF-31651941", "AF-31651942", "AF-31651943", "AF-31651944",
    "AF-31652157", "AF-31652159", "AF-31652160",
    "AF-31652406", "AF-31652407", "AF-31652408", "AF-31652409", "AF-31652410",
}

# Карточки, которые после пересборок KIT получили новый AF-SKU.
MOVED_OVERRIDES = {
    "AF-31652315": "NR147",
    "AF-31652567": "NR109",
    "AF-31652568": "NR109",
    "AF-31652569": "NR146",
    "AF-31652570": "NR164",
    "AF-31652571": "NR165",
}


def clean(value):
    return str(value or "").strip()


def expected_af(seq):
    seq = int(seq)
    if 0 <= seq <= 495:
        return f"AF-{31651449 + seq}"
    if 496 <= seq <= 508:
        return f"AF-{31651652 + seq}"
    if 509 <= seq <= 537:
        return f"AF-{31651873 + seq}"
    return ""


def load_data():
    price_meta = json.loads(PRICE_FILE.read_text(encoding="utf-8"))
    prices = {
        clean(k).upper(): float(v)
        for k, v in (price_meta.get("prices") or {}).items()
        if clean(k) and v not in (None, "")
    }
    registry = json.loads(REGISTRY_FILE.read_text(encoding="utf-8")).get("variants") or []

    sku_to_article = {}
    for item in registry:
        sku = expected_af(item.get("seq"))
        article = clean(item.get("article")).upper()
        if not sku or not article or sku in UNSAFE_EXPECTED_SKUS:
            continue
        sku_to_article[sku.upper()] = article

    for sku, article in MOVED_OVERRIDES.items():
        sku_to_article[sku.upper()] = article.upper()

    return price_meta, prices, registry, sku_to_article


def sheet_client():
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return gspread.authorize(creds)


def as_number(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except Exception:
        return None


def sync_sheet(ws, price_col, sku_to_article, prices):
    rows = ws.row_count
    bcol = ws.get(f"B1:B{rows}")
    pcol = ws.get(f"{price_col}1:{price_col}{rows}")

    recognized = 0
    missing_price = []
    updates = []
    unchanged = 0
    changed = []

    for idx in range(1, len(bcol)):
        sku = clean(bcol[idx][0] if bcol[idx] else "").upper()
        article = sku_to_article.get(sku)
        if not article:
            continue
        recognized += 1
        target = prices.get(article)
        if target is None:
            missing_price.append({"row": idx + 1, "sku": sku, "article": article})
            continue

        current_raw = pcol[idx][0] if idx < len(pcol) and pcol[idx] else None
        current = as_number(current_raw)
        if current is not None and abs(current - target) < 0.0001:
            unchanged += 1
            continue

        value = int(target) if float(target).is_integer() else target
        updates.append({"range": f"{price_col}{idx + 1}", "values": [[value]]})
        changed.append({
            "row": idx + 1,
            "sku": sku,
            "article": article,
            "old": current_raw,
            "new": value,
        })

    if recognized < 500:
        raise RuntimeError(
            f"Safety stop: only {recognized} Raduga SKU mappings found on sheet {ws.title}"
        )

    for pos in range(0, len(updates), 200):
        ws.batch_update(updates[pos:pos + 200], value_input_option="USER_ENTERED")

    return {
        "sheet": ws.title,
        "recognized": recognized,
        "updated": len(updates),
        "unchanged": unchanged,
        "missing_price": missing_price,
        "changes": changed,
    }


def main():
    price_meta, prices, registry, sku_to_article = load_data()
    gc = sheet_client()
    sh = gc.open_by_key(SHEET_ID)

    goods = sync_sheet(sh.worksheet("Товары"), "E", sku_to_article, prices)
    search = sync_sheet(sh.worksheet("Поиск"), "H", sku_to_article, prices)

    report = {
        "source_file": price_meta.get("source_file"),
        "effective_received": price_meta.get("effective_received"),
        "registry_variants": len(registry),
        "price_articles": len(prices),
        "safe_sku_mappings": len(sku_to_article),
        "unsafe_expected_skus": sorted(UNSAFE_EXPECTED_SKUS),
        "moved_overrides": MOVED_OVERRIDES,
        "goods": goods,
        "search": search,
    }
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "source_file": report["source_file"],
        "registry_variants": report["registry_variants"],
        "price_articles": report["price_articles"],
        "safe_sku_mappings": report["safe_sku_mappings"],
        "goods_recognized": goods["recognized"],
        "goods_updated": goods["updated"],
        "goods_unchanged": goods["unchanged"],
        "goods_missing_price": len(goods["missing_price"]),
        "search_recognized": search["recognized"],
        "search_updated": search["updated"],
        "search_unchanged": search["unchanged"],
        "search_missing_price": len(search["missing_price"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
