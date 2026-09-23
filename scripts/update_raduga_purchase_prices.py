#!/usr/bin/env python3
import json
import os
import re
import statistics
import sys
import unicodedata
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from gspread.cell import Cell
from rapidfuzz import fuzz, process

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "kit-backup" / "raduga_import_registry.json"
PRICES_PATH = ROOT / "kit-backup" / "raduga_purchase_prices.json"
OUT_DIR = ROOT / "runtime"
OUT_DIR.mkdir(exist_ok=True)
OUT_PATH = OUT_DIR / "raduga_purchase_update_report.json"

SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1xZUeaWUr-6O3KK0MO-1U7IGBKXXfgcmZ-EJ-w-2wWug")
APPLY = os.getenv("APPLY", "").strip().lower() in {"1", "true", "yes"}

# Historical KIT assignment blocks reconstructed from the original import registry
# and current KIT rows. Within each block AF IDs preserve import-registry order.
BLOCKS = [
    (0, 495, 31651449),
    (496, 508, 31651652),
    (509, 537, 31651873),
]

def clean(v):
    return str(v or "").strip()

def norm(v):
    s = unicodedata.normalize("NFKC", clean(v)).casefold().replace("ё", "е")
    repl = {
        "бежевая": "светлая",
        "бежевый": "светлый",
        "бежевые": "светлые",
        "мёд": "мед",
        "шоколад": "шоколад",
    }
    for a, b in repl.items():
        s = s.replace(a, b)
    s = s.replace("–", "-").replace("—", "-").replace("×", "x")
    s = re.sub(r"[^0-9a-zа-я/]+", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()

def expected_af(seq):
    for lo, hi, offset in BLOCKS:
        if lo <= seq <= hi:
            return f"AF-{offset + seq}"
    return ""

def sheet_client():
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return gspread.authorize(creds)

def main():
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["variants"]
    price_payload = json.loads(PRICES_PATH.read_text(encoding="utf-8"))
    prices = {clean(k).upper(): float(v) for k, v in price_payload["prices"].items()}

    gc = sheet_client()
    ws = gc.open_by_key(SHEET_ID).worksheet("Товары")
    rows = ws.get(f"A1:E{ws.row_count}")
    by_sku = {}
    af_candidates = []
    for idx, row in enumerate(rows[1:], start=2):
        sku = clean(row[1] if len(row) > 1 else "")
        name = clean(row[2] if len(row) > 2 else "")
        old_purchase = clean(row[4] if len(row) > 4 else "")
        if not sku:
            continue
        rec = {
            "row": idx,
            "id_kit": clean(row[0] if len(row) > 0 else ""),
            "sku": sku,
            "name": name,
            "name_norm": norm(name),
            "old_purchase": old_purchase,
        }
        by_sku[sku.upper()] = rec
        m = re.fullmatch(r"AF-(\d+)", sku, re.I)
        if m and 31650000 <= int(m.group(1)) <= 31653000:
            af_candidates.append(rec)

    mapped = []
    missing_expected = []
    mismatched_expected = []
    used_skus = set()

    for src in registry:
        seq = int(src["seq"])
        exp = expected_af(seq)
        article = clean(src["article"]).upper()
        cur = by_sku.get(exp.upper())
        if not cur:
            missing_expected.append({**src, "expected_sku": exp})
            continue
        score = float(fuzz.token_set_ratio(norm(src["name"]), cur["name_norm"]))
        item = {
            **src,
            "expected_sku": exp,
            "kit_row": cur["row"],
            "kit_id": cur["id_kit"],
            "kit_sku": cur["sku"],
            "kit_name": cur["name"],
            "old_purchase": cur["old_purchase"],
            "new_purchase": prices.get(article),
            "name_score": round(score, 2),
        }
        # Low similarity means the AF slot is occupied by another supplier/item.
        if score < 72:
            mismatched_expected.append(item)
            continue
        mapped.append(item)
        used_skus.add(cur["sku"].upper())

    # For expected slots that are absent/mismatched, report best name candidates only.
    candidate_names = [x["name_norm"] for x in af_candidates]
    suggestions = []
    for src in (missing_expected + mismatched_expected):
        q = norm(src["name"])
        top = process.extract(q, candidate_names, scorer=fuzz.token_set_ratio, limit=3)
        sug = []
        for _, score, pos in top:
            c = af_candidates[pos]
            if c["sku"].upper() in used_skus:
                continue
            sug.append({"sku": c["sku"], "name": c["name"], "row": c["row"], "score": round(float(score), 2)})
        suggestions.append({
            "seq": src["seq"],
            "rdg_sku": src["rdg_sku"],
            "article": src["article"],
            "name": src["name"],
            "expected_sku": src.get("expected_sku"),
            "candidates": sug[:3],
        })

    scores = [x["name_score"] for x in mapped]
    missing_price = [x for x in mapped if x["new_purchase"] is None]
    changes = []
    for x in mapped:
        new = x["new_purchase"]
        old = x["old_purchase"]
        same = False
        try:
            same = abs(float(str(old).replace(",", ".")) - float(new)) < 1e-9 if old != "" else False
        except Exception:
            same = False
        if not same:
            changes.append(x)

    report = {
        "apply": APPLY,
        "registry_variants": len(registry),
        "current_af_candidates": len(af_candidates),
        "mapped": len(mapped),
        "missing_expected": len(missing_expected),
        "mismatched_expected": len(mismatched_expected),
        "missing_purchase_price": len(missing_price),
        "cells_to_change": len(changes),
        "score_min": min(scores) if scores else None,
        "score_median": statistics.median(scores) if scores else None,
        "score_p10": sorted(scores)[max(0, int(len(scores)*0.10)-1)] if scores else None,
        "mapped_rows": mapped,
        "unresolved": suggestions,
    }

    # Hard safety checks before any write.
    if APPLY:
        if len(mapped) < 520:
            raise RuntimeError(f"Safety stop: only {len(mapped)} variants mapped (expected at least 520)")
        if missing_price:
            raise RuntimeError(f"Safety stop: {len(missing_price)} mapped variants have no new purchase price")
        if scores and min(scores) < 72:
            raise RuntimeError(f"Safety stop: name score below threshold: {min(scores)}")
        cells = [Cell(x["kit_row"], 5, x["new_purchase"]) for x in changes]
        if cells:
            ws.update_cells(cells, value_input_option="RAW")
        report["updated_cells"] = len(cells)

    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {k: report[k] for k in (
        "apply", "registry_variants", "current_af_candidates", "mapped",
        "missing_expected", "mismatched_expected", "missing_purchase_price",
        "cells_to_change", "score_min", "score_p10", "score_median"
    )}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nLowest mapped scores:")
    for x in sorted(mapped, key=lambda z: z["name_score"])[:25]:
        print(f'{x["name_score"]:5.1f} {x["article"]:6s} {x["kit_sku"]} | {x["name"]} -> {x["kit_name"]}')
    print("\nUnresolved:")
    for x in suggestions[:30]:
        print(json.dumps(x, ensure_ascii=False))

if __name__ == "__main__":
    main()
