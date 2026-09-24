#!/usr/bin/env python3
import importlib.util
import json
import os
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = os.environ["CATALOG_SPREADSHEET_ID"]
SHEET_NAME = "Норден"
SA_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
OUT = Path("catalog/norden_instock_chairs_missing_from_catalog.json")
SYNC_PATH = Path("norden-kit/sync_norden_kit.py")

def s(v):
    return str(v or "").strip()

def norm_raw(v):
    return unicodedata.normalize("NFKC", s(v)).casefold()

def norm_name(v):
    x = unicodedata.normalize("NFKC", s(v)).casefold().replace("ё", "е")
    x = re.sub(r"[^0-9a-zа-я]+", " ", x)
    return re.sub(r"\s+", " ", x).strip()

def name_tokens(v):
    stop = {
        "кресло","кресла","офисное","офисный","для","руководителя","персонала",
        "стул","стулья","стула","norden","с","и","на","в","цвет","модель"
    }
    return {t for t in norm_name(v).split() if len(t) > 1 and t not in stop}

def name_similarity(a, b):
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return 0.0
    seq = SequenceMatcher(None, na, nb).ratio() * 100
    ta, tb = name_tokens(a), name_tokens(b)
    if ta and tb:
        inter = len(ta & tb)
        token_f1 = (2 * inter / (len(ta) + len(tb))) * 100
    else:
        token_f1 = 0.0
    return max(seq, token_f1)

def load_sync():
    spec = importlib.util.spec_from_file_location("norden_sync", SYNC_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load Norden sync module")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def category_match(item):
    parts = [s(x).casefold().replace("ё","е") for x in (item.get("category_path") or [])]
    return any(("кресл" in p) or ("стул" in p) for p in parts)

mod = load_sync()
source, source_dups = mod.source_from_xml(short=False)
target = {article:item for article,item in source.items() if category_match(item)}
if not target:
    raise RuntimeError("Не найдены кресла/стулья в полном Norden.xml")

creds = json.loads(SA_JSON)
gc = gspread.authorize(Credentials.from_service_account_info(
    creds,
    scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"],
))
ws = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
vals = ws.get_all_values()
if not vals:
    raise RuntimeError("Вкладка Норден пуста")
headers = vals[0]
ai = headers.index("Артикул")
ni = headers.index("Название")
yi = headers.index("YML ID")

table = []
for rowno, r in enumerate(vals[1:], start=2):
    table.append({
        "row": rowno,
        "article": s(r[ai] if ai < len(r) else ""),
        "name": s(r[ni] if ni < len(r) else ""),
        "yml": s(r[yi] if yi < len(r) else ""),
    })

raw_yml = {norm_raw(r["yml"]) for r in table if r["yml"]}
items = []
exact_raw_skipped = 0

for supplier_article, item in target.items():
    supplier_name = s(item.get("name")) or supplier_article
    # Exact literal YML ID needs no manual review.
    if norm_raw(supplier_article) in raw_yml:
        exact_raw_skipped += 1
        continue

    ca = mod.norm_code(supplier_article)
    scored = []
    for tr in table:
        cb = mod.norm_code(tr["yml"])
        code_score = 0.0
        prefix = False
        normalized_equal = False
        if ca and cb:
            if ca == cb:
                code_score = 100.0
                normalized_equal = True
            else:
                ratio = SequenceMatcher(None, ca, cb).ratio() * 100
                code_score = ratio
                mn, mx = min(len(ca), len(cb)), max(len(ca), len(cb))
                if mn >= 6 and (ca.startswith(cb) or cb.startswith(ca)):
                    prefix = True
                    code_score = max(code_score, 92 + 7 * (mn / mx))
                elif mn >= 6 and (ca in cb or cb in ca):
                    code_score = max(code_score, 86 + 10 * (mn / mx))

        ns = name_similarity(supplier_name, tr["name"])
        tn_code = mod.norm_code(tr["name"])
        embedded = bool(ca and len(ca) >= 6 and ca in tn_code)

        if cb:
            combined = 0.74 * code_score + 0.26 * ns
        else:
            combined = 0.88 * ns
        if normalized_equal:
            combined = max(combined, 99.0)
        if prefix:
            combined = max(combined, 94.0)
        if embedded:
            combined = max(combined, 96.0 if ns >= 40 else 91.0)

        scored.append((combined, code_score, ns, embedded, prefix, normalized_equal, tr))

    scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    if not scored:
        continue
    best = scored[0]
    combined, code_score, ns, embedded, prefix, normalized_equal, tr = best

    # Keep only plausible manual-review candidates.
    plausible = (
        normalized_equal or prefix or embedded or
        (code_score >= 62 and ns >= 35) or
        (code_score >= 48 and ns >= 68) or
        (ns >= 84)
    )
    if not plausible or combined < 64:
        continue

    reasons = []
    if normalized_equal:
        reasons.append("YML ID совпадает после нормализации кириллицы/латиницы и разделителей")
    elif prefix:
        reasons.append("один YML ID похож на обрезанную версию другого")
    elif code_score >= 70:
        reasons.append("схожий YML ID / артикул")
    if embedded:
        reasons.append("артикул поставщика найден в названии товара таблицы")
    if ns >= 72:
        reasons.append("похожее название")
    if not reasons:
        reasons.append("частичное совпадение кода и названия")

    level = "Высокое" if combined >= 90 else ("Среднее" if combined >= 75 else "Низкое")
    alt = None
    if len(scored) > 1:
        second = scored[1]
        if second[0] >= 68 and best[0] - second[0] <= 5:
            alt = {
                "table_article": second[6]["article"],
                "table_name": second[6]["name"],
                "table_yml_id": second[6]["yml"],
                "score": round(second[0], 1),
            }

    items.append({
        "supplier_name": supplier_name,
        "supplier_yml_id": supplier_article,
        "table_article": tr["article"],
        "table_name": tr["name"],
        "table_yml_id": tr["yml"],
        "score": round(combined, 1),
        "level": level,
        "code_score": round(code_score, 1),
        "name_score": round(ns, 1),
        "reason": "; ".join(reasons),
        "alternative": alt,
        "table_row": tr["row"],
    })

items.sort(key=lambda x: (-x["score"], x["supplier_name"].casefold(), x["supplier_yml_id"].casefold()))
report = {
    "ok": True,
    "mode": "read-only fuzzy comparison",
    "source": "fresh Norden.xml + Norden.group -K8%.xml via source_from_xml",
    "scope": "all Norden chair/stool categories, stock is NOT a filter",
    "source_products_total": len(source),
    "source_duplicate_articles": len(source_dups),
    "source_chairs_stools": len(target),
    "catalog_rows": len(table),
    "exact_literal_yml_matches_skipped": exact_raw_skipped,
    "potential_matches": len(items),
    "items": items,
}
OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k:v for k,v in report.items() if k != "items"}, ensure_ascii=False, indent=2))
