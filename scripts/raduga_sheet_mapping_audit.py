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
OUT = ROOT / "runtime"
OUT.mkdir(exist_ok=True)
OUT_FILE = OUT / "raduga_mapping_audit.json"

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
    s = re.sub(r"\s+", " ", s).strip()
    return s

def load_old_offers():
    r = requests.get(OLD_FEED_URL, timeout=(20, 180), headers={"User-Agent": "Megapolis-Raduga-Audit/1.0"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    offers = []
    for offer in root.findall(".//offer"):
        name = clean(offer.findtext("name"))
        vendor = clean(offer.findtext("vendorCode"))
        params = {}
        for p in offer.findall("param"):
            params[clean(p.attrib.get("name"))] = clean(p.text)
        article = params.get("Артикул поставщика") or params.get("Артикул строки остатков")
        if not name or not article:
            continue
        # "PR512 PR513" in stock-row article is not a single purchase article; prefer explicit supplier article.
        article = article.split()[0]
        offers.append({
            "offer_id": clean(offer.attrib.get("id")),
            "vendor_code": vendor,
            "name": name,
            "name_norm": norm(name),
            "article": article,
        })
    return offers

def sheet_client():
    raw = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    info = json.loads(raw)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)

def main():
    old = load_old_offers()
    gc = sheet_client()
    ws = gc.open_by_key(SHEET_ID).worksheet("Товары")
    rows = ws.row_count
    abc = ws.get(f"A1:C{rows}")
    kcol = ws.get(f"K1:K{rows}")

    candidates = []
    for i in range(1, max(len(abc), len(kcol))):
        row = abc[i] if i < len(abc) else []
        kit_id = clean(row[0] if len(row) > 0 else "")
        sku = clean(row[1] if len(row) > 1 else "")
        name = clean(row[2] if len(row) > 2 else "")
        variant_id = clean((kcol[i][0] if i < len(kcol) and kcol[i] else ""))
        m = re.fullmatch(r"AF-(\d+)", sku, re.I)
        if not m or not name:
            continue
        n = int(m.group(1))
        # Радуга была загружена в этой исторической серии AF-артикулов.
        # Диапазон intentionally широкий; финальная связь подтверждается названием варианта.
        if 31650000 <= n <= 31654000:
            candidates.append({
                "row": i + 1,
                "kit_id": kit_id,
                "sku": sku,
                "name": name,
                "name_norm": norm(name),
                "variant_id": variant_id,
            })

    names = [x["name_norm"] for x in candidates]
    exact_index = defaultdict(list)
    for idx, value in enumerate(names):
        exact_index[value].append(idx)

    proposed = []
    ambiguous = []
    unmatched = []
    for off in old:
        exact = exact_index.get(off["name_norm"], [])
        if len(exact) == 1:
            c = candidates[exact[0]]
            proposed.append({**off, **{f"kit_{k}": v for k, v in c.items()}, "score": 100.0, "method": "exact"})
            continue

        top = process.extract(off["name_norm"], names, scorer=fuzz.token_set_ratio, limit=3)
        if not top:
            unmatched.append(off)
            continue
        best_name, best_score, best_idx = top[0]
        second_score = top[1][1] if len(top) > 1 else 0
        c = candidates[best_idx]
        rec = {
            **off,
            **{f"kit_{k}": v for k, v in c.items()},
            "score": round(float(best_score), 2),
            "second_score": round(float(second_score), 2),
            "margin": round(float(best_score - second_score), 2),
            "method": "fuzzy",
        }
        if best_score >= 88 and (best_score - second_score) >= 2.5:
            proposed.append(rec)
        else:
            ambiguous.append(rec)

    # One KIT row must never be assigned to two distinct old offers.
    by_variant = defaultdict(list)
    for rec in proposed:
        key = rec.get("kit_variant_id") or rec.get("kit_kit_id") or rec.get("kit_row")
        by_variant[key].append(rec)

    accepted = []
    duplicates = []
    for key, group in by_variant.items():
        if len(group) == 1:
            accepted.append(group[0])
            continue
        group = sorted(group, key=lambda x: (x.get("score", 0), x.get("margin", 0)), reverse=True)
        accepted.append(group[0])
        duplicates.extend(group[1:])

    article_counts = defaultdict(int)
    for x in accepted:
        article_counts[x["article"]] += 1

    report = {
        "old_offers": len(old),
        "candidate_kit_rows": len(candidates),
        "accepted": len(accepted),
        "exact": sum(1 for x in accepted if x["method"] == "exact"),
        "fuzzy": sum(1 for x in accepted if x["method"] == "fuzzy"),
        "ambiguous": len(ambiguous),
        "unmatched": len(unmatched),
        "duplicate_assignments_rejected": len(duplicates),
        "articles_matched": len(article_counts),
        "accepted_rows": accepted,
        "ambiguous_rows": ambiguous,
        "unmatched_old_offers": unmatched,
        "duplicate_rows": duplicates,
    }
    OUT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({k: report[k] for k in (
        "old_offers", "candidate_kit_rows", "accepted", "exact", "fuzzy",
        "ambiguous", "unmatched", "duplicate_assignments_rejected", "articles_matched"
    )}, ensure_ascii=False, indent=2))
    print("\nLowest accepted fuzzy matches:")
    lows = sorted((x for x in accepted if x["method"] == "fuzzy"), key=lambda x: x["score"])[:25]
    for x in lows:
        print(f'{x["score"]:5.1f} margin={x.get("margin",0):4.1f} {x["article"]:6s} | {x["name"]} -> {x["kit_name"]} [{x["kit_sku"]}]')
    print("\nAmbiguous sample:")
    for x in ambiguous[:25]:
        print(f'{x["score"]:5.1f} margin={x.get("margin",0):4.1f} {x["article"]:6s} | {x["name"]} -> {x["kit_name"]} [{x["kit_sku"]}]')

if __name__ == "__main__":
    main()
