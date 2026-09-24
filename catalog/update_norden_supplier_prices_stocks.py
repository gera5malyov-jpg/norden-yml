#!/usr/bin/env python3
import io, json, os, re, time, unicodedata
from pathlib import Path
import xml.etree.ElementTree as ET

import requests
import gspread
from google.oauth2.service_account import Credentials

PRICE_XML_URL = "https://norden.group/index.php?dispatch=sw_user_prices.get_file&file=Norden.group+-K8%25.xml"
SPREADSHEET_ID = os.environ["CATALOG_SPREADSHEET_ID"].strip()
SHEET_NAME = os.environ.get("CATALOG_SHEET","Норден").strip()
SA_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
REPORT = Path("catalog/norden_supplier_price_stock_report.json")

COLS = ["Закупка","РРЦ поставщика","Остаток МСК","Остаток СПБ","Остаток всего"]

def s(v): return str(v or "").strip()
def norm(v): return unicodedata.normalize("NFKC",s(v)).casefold()
def num(v):
    x=s(v).replace("\xa0"," ").replace(" ","").replace(",",".")
    if not x: return None
    try:
        f=float(x)
        return int(f) if f.is_integer() else f
    except: return None

def download():
    r=requests.get(PRICE_XML_URL,headers={"User-Agent":"Mozilla/5.0"},timeout=180)
    r.raise_for_status()
    return r.content

raw=download()

# Feed is windows-1251; ElementTree handles XML declaration correctly from bytes.
root=ET.fromstring(raw)

source={}
duplicates=[]
for n in root.iter("Номенклатура"):
    article=s(n.findtext("Артикул"))
    if not article:
        continue
    prices={}
    for p in n.findall("Цена"):
        kind=s(p.attrib.get("ВидЦен"))
        if kind:
            prices[kind]=num(p.text)
    stocks={}
    for st in n.findall("СвободныйОстаток"):
        wh=s(st.attrib.get("Склад"))
        if wh:
            stocks[wh]=num(st.text)
    row={
        "article":article,
        "rrp":prices.get("РРЦ"),
        "opt":prices.get("Опт"),
        "msk":stocks.get("Основной склад"),
        "spb":stocks.get("Питер Основной склад"),
    }
    vals=[x for x in (row["msk"],row["spb"]) if isinstance(x,(int,float))]
    row["total"]=sum(vals) if vals else None
    k=norm(article)
    if k in source:
        duplicates.append(article)
    source[k]=row

creds=json.loads(SA_JSON)
gc=gspread.authorize(Credentials.from_service_account_info(
    creds,scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
))
sh=gc.open_by_key(SPREADSHEET_ID)
ws=sh.worksheet(SHEET_NAME)

values=ws.get_all_values()
if not values:
    raise RuntimeError("Норден sheet is empty")

headers=list(values[0])
for c in COLS:
    if c not in headers:
        headers.append(c)

if ws.col_count < len(headers):
    ws.resize(cols=len(headers))
ws.update(
    range_name=f"A1:{gspread.utils.rowcol_to_a1(1,len(headers))}",
    values=[headers],
    value_input_option="RAW"
)
idx={h:i for i,h in enumerate(headers)}
if "YML ID" not in idx:
    raise RuntimeError("YML ID column is missing")

# Build a full rectangular rewrite only for new supplier columns; never add rows.
updates=[]
matched=0
unmatched=[]
filled={"Закупка":0,"РРЦ поставщика":0,"Остаток МСК":0,"Остаток СПБ":0,"Остаток всего":0}

for rowno,row in enumerate(values[1:],start=2):
    yml_id=s(row[idx["YML ID"]] if idx["YML ID"]<len(row) else "")
    article=s(row[idx["Артикул"]] if idx["Артикул"]<len(row) else "")
    src=source.get(norm(yml_id)) if yml_id else None
    if not src:
        unmatched.append({"row":rowno,"article":article,"yml_id":yml_id})
        # Clear only the supplier price/stock cells to avoid stale supplier values.
        vals=["","","","",""]
    else:
        matched+=1
        vals=[
            src["opt"] if src["opt"] is not None else "",
            src["rrp"] if src["rrp"] is not None else "",
            src["msk"] if src["msk"] is not None else "",
            src["spb"] if src["spb"] is not None else "",
            src["total"] if src["total"] is not None else "",
        ]
        for c,v in zip(COLS,vals):
            if v!="": filled[c]+=1
    updates.append((rowno,vals))

# Write in contiguous batches, one range per batch.
first_col=idx[COLS[0]]+1
last_col=idx[COLS[-1]]+1
batch_size=200
for i in range(0,len(updates),batch_size):
    batch=updates[i:i+batch_size]
    r1=batch[0][0]; r2=batch[-1][0]
    vals=[x[1] for x in batch]
    ws.update(
        range_name=f"{gspread.utils.rowcol_to_a1(r1,first_col)}:{gspread.utils.rowcol_to_a1(r2,last_col)}",
        values=vals,
        value_input_option="RAW"
    )

report={
    "ok":True,
    "source_url":PRICE_XML_URL,
    "source_items":len(source),
    "source_duplicates":len(duplicates),
    "catalog_rows":len(values)-1,
    "matched_existing_rows":matched,
    "unmatched_existing_rows":len(unmatched),
    "unmatched_sample":unmatched[:100],
    "filled":filled,
    "rules":{
        "Закупка":"Цена ВидЦен=Опт",
        "РРЦ поставщика":"Цена ВидЦен=РРЦ",
        "Остаток МСК":"СвободныйОстаток, Склад=Основной склад",
        "Остаток СПБ":"СвободныйОстаток, Склад=Питер Основной склад",
        "Остаток всего":"Остаток МСК + Остаток СПБ"
    },
    "new_rows_created":0
}
REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(report,ensure_ascii=False))
