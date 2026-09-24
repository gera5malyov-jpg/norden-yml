#!/usr/bin/env python3
import json, os, time, unicodedata
from pathlib import Path
import requests, gspread
from google.oauth2.service_account import Credentials

KIT_BASE="https://api.kit.yandex.net"
KIT_TOKEN=os.environ["YANDEX_KIT_TOKEN"].strip()
SPREADSHEET_ID=os.environ["CATALOG_SPREADSHEET_ID"].strip()
SA_JSON=os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
SHEET_NAME=os.environ.get("CATALOG_SHEET","Норден").strip()
REPORT=Path("catalog/norden_prices_report.json")

COLS=["Закупка","Цена продажи Webasyst","Цена продажи KIT","Зачёркнутая цена"]

def s(v): return str(v or "").strip()
def nk(v): return unicodedata.normalize("NFKC",s(v)).casefold()

def pick(obj,*paths):
    for path in paths:
        cur=obj
        ok=True
        for key in path.split("."):
            if not isinstance(cur,dict) or key not in cur:
                ok=False; break
            cur=cur[key]
        if ok and cur not in (None,""):
            return cur
    return ""

def kit_request(session,path,params=None):
    headers={"Authorization":f"Bearer {KIT_TOKEN}","Accept":"application/json"}
    for attempt in range(10):
        r=session.get(KIT_BASE+path,headers=headers,params=params,timeout=120)
        if r.status_code==429:
            time.sleep(float(r.headers.get("Retry-After") or min(20,2+attempt))); continue
        if r.status_code>=500:
            time.sleep(min(15,2**attempt)); continue
        r.raise_for_status()
        return r.json() if r.content else {}
    raise RuntimeError("KIT retries exhausted")

def items(d):
    if isinstance(d,list): return d
    if not isinstance(d,dict): return []
    for k in ("items","variants","results","data"):
        v=d.get(k)
        if isinstance(v,list): return v
        if isinstance(v,dict) and isinstance(v.get("items"),list): return v["items"]
    return []

def total(d):
    if not isinstance(d,dict): return None
    for k in ("total","total_count"):
        if isinstance(d.get(k),int): return d[k]
    m=d.get("meta")
    if isinstance(m,dict):
        for k in ("total","total_count"):
            if isinstance(m.get(k),int): return m[k]
    return None

creds=json.loads(SA_JSON)
gc=gspread.authorize(Credentials.from_service_account_info(
    creds,scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
))
sh=gc.open_by_key(SPREADSHEET_ID)
ws=sh.worksheet(SHEET_NAME)
vals=ws.get_all_values()
headers=list(vals[0])
for c in COLS:
    if c not in headers: headers.append(c)
if ws.col_count<len(headers): ws.resize(cols=len(headers))
ws.update(range_name=f"A1:{gspread.utils.rowcol_to_a1(1,len(headers))}",values=[headers],value_input_option="RAW")
idx={h:i for i,h in enumerate(headers)}

# Build article row map
row_by_article={}
for rn,row in enumerate(vals[1:],start=2):
    a=s(row[idx["Артикул"]] if idx["Артикул"]<len(row) else "")
    if a: row_by_article[nk(a)]=rn

# Scan KIT variants once, match by sku to catalog articles.
session=requests.Session()
kit_by_article={}
page=1
while True:
    d=kit_request(session,"/v1/variants",{"page":page,"per_page":100})
    batch=items(d)
    for v in batch:
        if not isinstance(v,dict): continue
        sku=s(v.get("sku"))
        if sku and nk(sku) in row_by_article and nk(sku) not in kit_by_article:
            kit_by_article[nk(sku)]=v
    t=total(d)
    if not batch or (t is not None and page*100>=t) or (t is None and len(batch)<100):
        break
    page+=1

updates=[]
kit_price_count=0
purchase_count=0
wa_sale_count=0
crossed_count=0

for rn,row in enumerate(vals[1:],start=2):
    article=s(row[idx["Артикул"]] if idx["Артикул"]<len(row) else "")
    if not article: continue

    # Webasyst values already imported into explicit source columns.
    wa_purchase=s(row[idx["Закупочная цена Webasyst"]] if "Закупочная цена Webasyst" in idx and idx["Закупочная цена Webasyst"]<len(row) else "")
    wa_sale=s(row[idx["Цена Webasyst"]] if "Цена Webasyst" in idx and idx["Цена Webasyst"]<len(row) else "")
    wa_old=s(row[idx["Старая цена Webasyst"]] if "Старая цена Webasyst" in idx and idx["Старая цена Webasyst"]<len(row) else "")
    if wa_purchase:
        updates.append({"range":gspread.utils.rowcol_to_a1(rn,idx["Закупка"]+1),"values":[[wa_purchase]]}); purchase_count+=1
    if wa_sale:
        updates.append({"range":gspread.utils.rowcol_to_a1(rn,idx["Цена продажи Webasyst"]+1),"values":[[wa_sale]]}); wa_sale_count+=1

    kv=kit_by_article.get(nk(article))
    if kv:
        # KIT API variants can expose pricing either flat or nested.
        sale=pick(kv,
            "manual_discount_price","pricing.manual_discount_price",
            "discount_price","pricing.discount_price",
            "price_for_customer","pricing.price_for_customer",
            "sale_price","pricing.sale_price",
            "price","pricing.price")
        crossed=pick(kv,
            "old_price","pricing.old_price",
            "compare_price","pricing.compare_price",
            "base_price","pricing.base_price",
            "price","pricing.price")
        # If discounted price exists, the regular price is crossed-out.
        regular=pick(kv,"price","pricing.price")
        discounted=pick(kv,"manual_discount_price","pricing.manual_discount_price","discount_price","pricing.discount_price")
        if discounted not in (None,""):
            sale=discounted
            if regular not in (None,""): crossed=regular
        if sale not in (None,""):
            updates.append({"range":gspread.utils.rowcol_to_a1(rn,idx["Цена продажи KIT"]+1),"values":[[sale]]}); kit_price_count+=1
        if crossed not in (None,"") and str(crossed)!=str(sale):
            updates.append({"range":gspread.utils.rowcol_to_a1(rn,idx["Зачёркнутая цена"]+1),"values":[[crossed]]}); crossed_count+=1
    elif wa_old:
        # Fallback only when no KIT match exists.
        updates.append({"range":gspread.utils.rowcol_to_a1(rn,idx["Зачёркнутая цена"]+1),"values":[[wa_old]]}); crossed_count+=1

for i in range(0,len(updates),300):
    ws.batch_update(updates[i:i+300],value_input_option="RAW")

report={
    "ok":True,
    "catalog_rows":len(row_by_article),
    "kit_matches":len(kit_by_article),
    "purchase_filled":purchase_count,
    "webasyst_sale_filled":wa_sale_count,
    "kit_sale_filled":kit_price_count,
    "crossed_price_filled":crossed_count,
    "rule":"Закупка=Webasyst purchase_price when available; Цена продажи Webasyst=Webasyst sale price; Цена продажи KIT=KIT current/customer price; Зачёркнутая цена=KIT regular/old price when discount exists, fallback Webasyst compare_price only if no KIT match."
}
REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(report,ensure_ascii=False))
