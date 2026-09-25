#!/usr/bin/env python3
import json, os, time
import requests, gspread
from google.oauth2.service_account import Credentials

SID=os.environ["CATALOG_SPREADSHEET_ID"].strip()
SHEET=os.environ.get("CATALOG_SHEET","Норден").strip()
BID=os.environ.get("YANDEX_MARKET_BUSINESS_ID","20806099").strip()
TOKEN=os.environ["YANDEX_MARKET_API_KEY"].strip()
SA=os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

def s(v): return str(v or "").strip()
def chunks(a,n):
    for i in range(0,len(a),n): yield a[i:i+n]

def call(body):
    h={"Api-Key":TOKEN,"Content-Type":"application/json","Accept":"application/json"}
    url=f"https://api.partner.market.yandex.ru/v2/businesses/{BID}/offer-mappings"
    last=None
    for attempt in range(8):
        r=requests.post(url,headers=h,json=body,timeout=60)
        last=r
        if r.status_code in (420,429) or r.status_code>=500:
            time.sleep(float(r.headers.get("Retry-After") or min(65,2**attempt)))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Yandex API failed: {last.status_code if last else 'N/A'}")

info=json.loads(SA)
cr=Credentials.from_service_account_info(
    info,
    scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
)
gc=gspread.authorize(cr)
ws=gc.open_by_key(SID).worksheet(SHEET)
vals=ws.get_all_values()
headers=[s(x) for x in vals[0]]
idx={h:i for i,h in enumerate(headers)}
need=["Артикул","Yandex категория","Yandex category_id"]
miss=[x for x in need if x not in idx]
if miss:
    raise RuntimeError("Нет колонок: "+", ".join(miss))

rows=[]
for rn,row in enumerate(vals[1:],2):
    art=s(row[idx["Артикул"]] if idx["Артикул"]<len(row) else "")
    if art: rows.append((rn,art))

mapping={}
for batch in chunks(sorted({a for _,a in rows}),100):
    d=call({"offerIds":batch})
    for x in ((d.get("result") or {}).get("offerMappings") or []):
        off=x.get("offer") or {}
        mp=x.get("mapping") or {}
        oid=s(off.get("offerId"))
        if not oid: continue
        mapping[oid]={
            "name":s(mp.get("marketCategoryName") or off.get("marketCategoryName")),
            "id":mp.get("marketCategoryId") or off.get("marketCategoryId") or ""
        }

found=0
name_col=[]
id_col=[]
for row in vals[1:]:
    current_name=s(row[idx["Yandex категория"]] if idx["Yandex категория"]<len(row) else "")
    current_id=s(row[idx["Yandex category_id"]] if idx["Yandex category_id"]<len(row) else "")
    art=s(row[idx["Артикул"]] if idx["Артикул"]<len(row) else "")
    m=mapping.get(art)
    if m and m["name"]:
        current_name=m["name"]
        found+=1
    if m and m["id"]:
        current_id=str(m["id"])
    name_col.append([current_name])
    id_col.append([current_id])

if name_col:
    start=2
    end=len(vals)
    name_letter=gspread.utils.rowcol_to_a1(1,idx["Yandex категория"]+1).rstrip("1")
    id_letter=gspread.utils.rowcol_to_a1(1,idx["Yandex category_id"]+1).rstrip("1")
    ws.update(range_name=f"{name_letter}{start}:{name_letter}{end}",values=name_col,value_input_option="RAW")
    ws.update(range_name=f"{id_letter}{start}:{id_letter}{end}",values=id_col,value_input_option="RAW")

report={"business_id":BID,"rows_with_article":len(rows),"mapping_found":len(mapping),"category_names_written":found}
with open("catalog/yandex_categories_report.json","w",encoding="utf-8") as f:
    json.dump(report,f,ensure_ascii=False,indent=2); f.write("\n")
print(json.dumps(report,ensure_ascii=False,indent=2))
