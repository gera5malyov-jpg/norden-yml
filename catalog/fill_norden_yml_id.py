#!/usr/bin/env python3
import importlib.util, json, os, unicodedata
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = ROOT / "norden-kit" / "sync_norden_kit.py"
REPORT = ROOT / "catalog" / "norden_yml_id_report.json"

SPREADSHEET_ID = os.environ["CATALOG_SPREADSHEET_ID"].strip()
SHEET_NAME = os.environ.get("CATALOG_SHEET", "Норден").strip()
SA_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
NORDEN_SECRET = os.environ.get("NORDEN_SECRET", "").strip()

def s(v):
    return str(v or "").strip()

def key(v):
    return unicodedata.normalize("NFKC", s(v)).casefold()

spec = importlib.util.spec_from_file_location("norden_sync_source", SRC_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

source = {}
duplicates = []
source_mode = ""
api_error = None

# Fast path: use the checked-in Norden XML/YML snapshot already in the repository.
local_feed = ROOT / "norden.yml"
try:
    import xml.etree.ElementTree as ET
    by_article = {}
    seen = set()
    dup = set()
    for event, elem in ET.iterparse(local_feed, events=("end",)):
        if elem.tag == "Номенклатура":
            article = s(elem.findtext("Артикул"))
            code = s(elem.findtext("Код"))
            if article:
                k = key(article)
                if k in seen:
                    dup.add(article)
                seen.add(k)
                by_article[k] = code
            elem.clear()
    if by_article:
        source_mode = "repo-norden.yml"
        duplicates = sorted(dup)
    else:
        raise RuntimeError("local norden.yml contained no nomenclature rows")
except Exception as exc:
    api_error = f"local-feed-fallback: {exc}"
    source, duplicates, source_mode, api_error2 = mod.load_source(NORDEN_SECRET, short=False)
    if api_error2:
        api_error = (api_error + "; " + api_error2) if api_error else api_error2
    by_article = {}
    for article, item in source.items():
        k = key(article)
        if not k:
            continue
        by_article[k] = s((item or {}).get("norden_code"))

creds = json.loads(SA_JSON)
gc = gspread.authorize(Credentials.from_service_account_info(
    creds,
    scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
))
sh = gc.open_by_key(SPREADSHEET_ID)
ws = sh.worksheet(SHEET_NAME)

values = ws.get_all_values()
if not values:
    raise RuntimeError("Sheet is empty")

headers = list(values[0])
if "YML ID" not in headers:
    headers.append("YML ID")
if ws.col_count < len(headers):
    ws.resize(cols=len(headers))
ws.update(
    range_name=f"A1:{gspread.utils.rowcol_to_a1(1, len(headers))}",
    values=[headers],
    value_input_option="RAW",
)
idx = {h:i for i,h in enumerate(headers)}
article_col = idx["Артикул"]
yml_col = idx["YML ID"]

updates = []
matched = 0
unmatched = []
blank_id = []
for row_no, row in enumerate(values[1:], start=2):
    article = s(row[article_col] if article_col < len(row) else "")
    if not article:
        continue
    yml_id = by_article.get(key(article), "")
    if yml_id:
        matched += 1
        updates.append({
            "range": gspread.utils.rowcol_to_a1(row_no, yml_col + 1),
            "values": [[yml_id]],
        })
    else:
        if key(article) in by_article:
            blank_id.append(article)
        else:
            unmatched.append(article)

for i in range(0, len(updates), 400):
    ws.batch_update(updates[i:i+400], value_input_option="RAW")

report = {
    "ok": True,
    "source_mode": source_mode,
    "api_error": api_error,
    "source_products": len(by_article),
    "source_duplicate_articles": len(duplicates),
    "catalog_rows_with_article": sum(1 for r in values[1:] if article_col < len(r) and s(r[article_col])),
    "matched_yml_id": matched,
    "unmatched_articles": len(unmatched),
    "unmatched_sample": unmatched[:100],
    "source_article_with_blank_yml_id": len(blank_id),
    "blank_yml_id_sample": blank_id[:100],
    "definition": "YML ID = Norden Код (norden_code) from current Norden API/XML, matched strictly by Артикул",
}
REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
