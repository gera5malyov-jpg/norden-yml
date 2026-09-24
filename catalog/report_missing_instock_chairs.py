#!/usr/bin/env python3
import json, os, re, unicodedata, requests
import xml.etree.ElementTree as ET
from pathlib import Path
import gspread
from google.oauth2.service_account import Credentials

PRICE_XML_URL = "https://norden.group/index.php?dispatch=sw_user_prices.get_file&file=Norden.group+-K8%25.xml"
SPREADSHEET_ID = os.environ["CATALOG_SPREADSHEET_ID"]
SHEET_NAME = "Норден"
SA_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
OUT = Path("catalog/norden_instock_chairs_missing_from_catalog.json")

def s(v): return str(v or "").strip()
def norm(v): return re.sub(r"[^0-9a-zа-яё]+","",unicodedata.normalize("NFKC",s(v)).casefold())
def num(v):
    x=s(v).replace("\xa0"," ").replace(" ","").replace(",",".")
    if not x: return 0
    try: return float(x)
    except: return 0

# Read catalog YML IDs
creds=json.loads(SA_JSON)
gc=gspread.authorize(Credentials.from_service_account_info(
    creds,scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
))
ws=gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
vals=ws.get_all_values()
headers=vals[0]
yi=headers.index("YML ID")
ai=headers.index("Артикул")
catalog_yml={norm(r[yi]) for r in vals[1:] if yi < len(r) and s(r[yi])}
catalog_articles={norm(r[ai]) for r in vals[1:] if ai < len(r) and s(r[ai])}

# Load supplier price/stock XML
raw=requests.get(PRICE_XML_URL,headers={"User-Agent":"Mozilla/5.0"},timeout=180).content
root=ET.fromstring(raw)

rows=[]
for n in root.iter("Номенклатура"):
    article=s(n.findtext("Артикул"))
    name=s(n.findtext("Наименование"))
    full=s(n.findtext("НаименованиеПолное"))
    title=full or name or article
    if not article:
        continue
    # Chair criterion: "кресл" in supplier name/full name.
    if "кресл" not in title.casefold():
        continue
    stocks={}
    for st in n.findall("СвободныйОстаток"):
        wh=s(st.attrib.get("Склад"))
        stocks[wh]=num(st.text)
    total=sum(stocks.values())
    if total <= 0:
        continue
    prices={}
    for p in n.findall("Цена"):
        prices[s(p.attrib.get("ВидЦен"))]=num(p.text)
    # Catalog YML ID is supplier item code/article for these rows.
    k=norm(article)
    if k in catalog_yml or k in catalog_articles:
        continue
    rows.append({
        "yml_id":article,
        "name":title,
        "stock_total":int(total) if total.is_integer() else total,
        "stock_main":int(stocks.get("Основной склад",0)) if float(stocks.get("Основной склад",0)).is_integer() else stocks.get("Основной склад",0),
        "stock_spb":int(stocks.get("Питер Основной склад",0)) if float(stocks.get("Питер Основной склад",0)).is_integer() else stocks.get("Питер Основной склад",0),
        "opt":int(prices.get("Опт",0)) if float(prices.get("Опт",0)).is_integer() else prices.get("Опт",0),
        "rrp":int(prices.get("РРЦ",0)) if float(prices.get("РРЦ",0)).is_integer() else prices.get("РРЦ",0),
    })

rows.sort(key=lambda x:(-x["stock_total"], x["name"]))
report={
  "ok":True,
  "catalog_rows":len(vals)-1,
  "missing_instock_chairs_count":len(rows),
  "items":rows,
}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"count":len(rows),"top":rows[:20]},ensure_ascii=False))
