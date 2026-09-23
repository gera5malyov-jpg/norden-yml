#!/usr/bin/env python3
import json
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import gspread
import requests
from google.oauth2.service_account import Credentials
from rapidfuzz import fuzz, process

ROOT = Path(__file__).resolve().parents[1]
PRICE_FILE = ROOT / "kit-backup" / "raduga_purchase_prices.json"
OUT = ROOT / "runtime"
OUT.mkdir(exist_ok=True)
REPORT_FILE = OUT / "raduga_purchase_price_sync.json"

SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1xZUeaWUr-6O3KK0MO-1U7IGBKXXfgcmZ-EJ-w-2wWug")
OLD_FEED_URL = os.getenv(
    "RADUGA_OLD_FEED_URL",
    "https://drive.google.com/uc?export=download&id=137_vIKlKnYXmzKMod4CwSgUdt-u0GtZy",
)

def clean(value):
    return str(value or "").strip()

def norm(value):
    s = unicodedata.normalize("NFKC", clean(value)).casefold().replace("ё", "е")
    s = s.replace("–", "-").replace("—", "-").replace("х", "x")
    s = re.sub(r"[^0-9a-zа-я/]+", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()

def load_prices():
    data = json.loads(PRICE_FILE.read_text(encoding="utf-8"))
    prices = {clean(k): float(v) for k, v in data.get("prices", {}).items() if clean(k) and v not in (None, "")}
    return data, prices

def load_old_offers():
    r = requests.get(OLD_FEED_URL, timeout=(20, 180), headers={"User-Agent": "Megapolis-Raduga-PriceSync/1.0"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    offers = []
    for offer in root.findall(".//offer"):
        name = clean(offer.findtext("name"))
        params = {clean(p.attrib.get("name")): clean(p.text) for p in offer.findall("param")}
        article = params.get("Артикул поставщика") or params.get("Артикул строки остатков")
        if not name or not article:
            continue
        article = article.split()[0]
        offers.append({
            "offer_id": clean(offer.attrib.get("id")),
            "name": name,
            "name_norm": norm(name),
            "article": article,
        })
    return offers

def sheet_client():
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)

def as_number(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except Exception:
        return None

def main():
    price_meta, prices = load_prices()
    old = load_old_offers()
    gc = sheet_client()
    ws = gc.open_by_key(SHEET_ID).worksheet("Товары")

    rows = ws.row_count
    bc = ws.get(f"B1:C{rows}")
    ecol = ws.get(f"E1:E{rows}")

    candidates = []
    for i in range(1, len(bc)):
        row = bc[i] if i < len(bc) else []
        sku = clean(row[0] if len(row) > 0 else "")
        name = clean(row[1] if len(row) > 1 else "")
        if not name:
            continue
        m = re.fullmatch(r"AF-(\d+)", sku, re.I)
        if not m:
            continue
        n = int(m.group(1))
        if 31650000 <= n <= 31654000:
            candidates.append({
                "row": i + 1,
                "sku": sku,
                "name": name,
                "name_norm": norm(name),
            })

    names = [x["name_norm"] for x in candidates]
    exact_index = defaultdict(list)
    for idx, value in enumerate(names):
        exact_index[value].append(idx)

    proposed, ambiguous, unmatched = [], [], []
    for off in old:
        if off["article"] not in prices:
            continue
        exact = exact_index.get(off["name_norm"], [])
        if len(exact) == 1:
            c = candidates[exact[0]]
            proposed.append({**off, **c, "score": 100.0, "margin": 100.0, "method": "exact"})
            continue

        top = process.extract(off["name_norm"], names, scorer=fuzz.token_set_ratio, limit=3)
        if not top:
            unmatched.append(off)
            continue
        _best_name, best_score, best_idx = top[0]
        second_score = top[1][1] if len(top) > 1 else 0
        c = candidates[best_idx]
        rec = {
            **off,
            **c,
            "score": round(float(best_score), 2),
            "second_score": round(float(second_score), 2),
            "margin": round(float(best_score - second_score), 2),
            "method": "fuzzy",
        }
        if best_score >= 88 and (best_score - second_score) >= 2.5:
            proposed.append(rec)
        else:
            ambiguous.append(rec)

    by_row = defaultdict(list)
    for rec in proposed:
        by_row[rec["row"]].append(rec)

    accepted, duplicates = [], []
    for row_no, group in by_row.items():
        if len(group) == 1:
            accepted.append(group[0])
            continue
        group = sorted(group, key=lambda x: (x.get("score", 0), x.get("margin", 0)), reverse=True)
        accepted.append(group[0])
        duplicates.extend(group[1:])

    updates = []
    unchanged = 0
    update_rows = []
    for rec in accepted:
        target = prices.get(rec["article"])
        if target is None:
            continue
        current_raw = ecol[rec["row"] - 1][0] if rec["row"] - 1 < len(ecol) and ecol[rec["row"] - 1] else None
        current = as_number(current_raw)
        if current is not None and abs(current - target) < 0.0001:
            unchanged += 1
            continue
        value = int(target) if float(target).is_integer() else target
        updates.append({"range": f"E{rec['row']}", "values": [[value]]})
        update_rows.append({
            "row": rec["row"],
            "sku": rec["sku"],
            "article": rec["article"],
            "old": current_raw,
            "new": value,
            "score": rec["score"],
            "method": rec["method"],
        })

    for pos in range(0, len(updates), 200):
        ws.batch_update(updates[pos:pos + 200], value_input_option="USER_ENTERED")

    report = {
        "source_file": price_meta.get("source_file"),
        "effective_received": price_meta.get("effective_received"),
        "price_articles": len(prices),
        "old_offers_considered": len(old),
        "candidate_kit_rows": len(candidates),
        "accepted_mappings": len(accepted),
        "updated_rows": len(updates),
        "unchanged_rows": unchanged,
        "ambiguous_mappings_skipped": len(ambiguous),
        "unmatched_mappings_skipped": len(unmatched),
        "duplicate_assignments_rejected": len(duplicates),
        "updates": update_rows,
        "ambiguous_sample": ambiguous[:50],
        "unmatched_sample": unmatched[:50],
    }
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in (
        "source_file", "price_articles", "candidate_kit_rows", "accepted_mappings",
        "updated_rows", "unchanged_rows", "ambiguous_mappings_skipped",
        "unmatched_mappings_skipped", "duplicate_assignments_rejected"
    )}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
