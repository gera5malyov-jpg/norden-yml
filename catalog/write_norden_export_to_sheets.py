#!/usr/bin/env python3
import json, os
from pathlib import Path
import gspread
from google.oauth2.service_account import Credentials

SRC = Path("catalog/norden_export.json")
SPREADSHEET_ID = os.environ["CATALOG_SPREADSHEET_ID"].strip()
SHEET = os.environ.get("CATALOG_SHEET","Норден").strip()
SA_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

def j(x):
    return json.dumps(x, ensure_ascii=False, separators=(",",":"))

def split_json(obj, chunk=45000, max_parts=16):
    s = j(obj)
    parts = [s[i:i+chunk] for i in range(0,len(s),chunk)]
    if len(parts) > max_parts:
        parts = parts[:max_parts-1] + [s[(max_parts-1)*chunk:]]
    return parts + [""]*(max_parts-len(parts))

data = json.loads(SRC.read_text(encoding="utf-8"))
products = data.get("products") or []

headers = [
    "Артикул","Ozon product_id","Название","Бренд","Архивный",
    "Категория Ozon","Тип Ozon","Ширина","Высота","Глубина",
    "Ед. габаритов","Вес","Ед. веса","Основное фото","Фото",
    "Цена","Старая цена","Минимальная цена","Маркетинговая цена",
    "Остатки JSON","Атрибуты JSON","Комплексные атрибуты JSON",
    "API info JSON","API list JSON","API price JSON",
] + [f"Полный JSON {i}" for i in range(1,17)]

rows=[]
for p in products:
    a=p.get("attributes") or {}
    pr=p.get("price") or {}
    price=pr.get("price") or {}
    li=p.get("product_list") or {}
    inf=p.get("info") or {}
    st=p.get("stocks") or []
    full=p
    rows.append([
        p.get("offer_id",""),
        p.get("product_id",""),
        p.get("name",""),
        p.get("brand",""),
        "Да" if p.get("archived") else "Нет",
        a.get("description_category_id",""),
        a.get("type_id",""),
        a.get("width",""),
        a.get("height",""),
        a.get("depth",""),
        a.get("dimension_unit",""),
        a.get("weight",""),
        a.get("weight_unit",""),
        a.get("primary_image",""),
        j(a.get("images") or []),
        price.get("price",""),
        price.get("old_price",""),
        price.get("min_price",""),
        price.get("marketing_seller_price",""),
        j(st),
        j(a.get("attributes") or []),
        j(a.get("complex_attributes") or []),
        j(inf),
        j(li),
        j(pr),
    ] + split_json(full))

creds=json.loads(SA_JSON)
gc=gspread.authorize(Credentials.from_service_account_info(
    creds,
    scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
))
sh=gc.open_by_key(SPREADSHEET_ID)
try:
    ws=sh.worksheet(SHEET)
except gspread.WorksheetNotFound:
    ws=sh.add_worksheet(title=SHEET, rows=max(1000,len(rows)+20), cols=len(headers)+2)

needed_rows=max(1000,len(rows)+20)
needed_cols=len(headers)
if ws.row_count < needed_rows or ws.col_count < needed_cols:
    ws.resize(rows=max(ws.row_count,needed_rows), cols=max(ws.col_count,needed_cols))

ws.clear()
ws.update(range_name=f"A1:{gspread.utils.rowcol_to_a1(1,len(headers))}", values=[headers], value_input_option="RAW")

batch_size=20
for i in range(0,len(rows),batch_size):
    batch=rows[i:i+batch_size]
    start=i+2
    end=start+len(batch)-1
    rng=f"A{start}:{gspread.utils.rowcol_to_a1(end,len(headers))}"
    ws.update(range_name=rng, values=batch, value_input_option="RAW")

ws.freeze(rows=1)
try:
    ws.set_basic_filter()
except Exception:
    pass

report={
    "ok":True,
    "spreadsheet_id":SPREADSHEET_ID,
    "sheet":SHEET,
    "rows_written":len(rows),
    "archived_rows":sum(1 for p in products if p.get("archived")),
    "source_generated_at":data.get("generated_at"),
    "source_errors":data.get("errors"),
}
Path("catalog/sheet_write_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(report,ensure_ascii=False))
