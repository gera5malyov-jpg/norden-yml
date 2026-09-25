#!/usr/bin/env python3
import json, os, time
import requests, gspread
from google.oauth2.service_account import Credentials

SID=os.environ["CATALOG_SPREADSHEET_ID"].strip()
SHEET=os.environ.get("CATALOG_SHEET","Норден").strip()
CID=os.environ["OZON_CLIENT_ID"].strip()
KEY=os.environ["OZON_API_KEY"].strip()
SA=os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
BASE="https://api-seller.ozon.ru"

def s(v): return str(v or "").strip()

def post(path, body, retries=8):
    h={"Client-Id":CID,"Api-Key":KEY,"Content-Type":"application/json","Accept":"application/json"}
    last=None
    for attempt in range(retries):
        r=requests.post(BASE+path,headers=h,json=body,timeout=90)
        last=r
        if r.status_code==429 or r.status_code>=500:
            time.sleep(float(r.headers.get("Retry-After") or min(30,2**attempt)))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Ozon API failed: {last.status_code if last else 'N/A'}")

def flatten(nodes, out):
    for node in nodes or []:
        if not isinstance(node,dict):
            continue
        cid=node.get("description_category_id")
        name=s(node.get("category_name"))
        if cid and name:
            out[str(cid)]=name
        flatten(node.get("children") or [],out)

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
need=["Категория Ozon","Ozon категория"]
miss=[x for x in need if x not in idx]
if miss:
    raise RuntimeError("Нет колонок: "+", ".join(miss))

tree=post("/v1/description-category/tree",{"language":"DEFAULT"})
names={}
flatten(tree.get("result") or [],names)

out=[]
written=0
unknown={}
for row in vals[1:]:
    current=s(row[idx["Ozon категория"]] if idx["Ozon категория"]<len(row) else "")
    raw=s(row[idx["Категория Ozon"]] if idx["Категория Ozon"]<len(row) else "")
    if raw and raw in names:
        current=names[raw]
        written+=1
    elif raw:
        unknown[raw]=unknown.get(raw,0)+1
    out.append([current])

if out:
    col=gspread.utils.rowcol_to_a1(1,idx["Ozon категория"]+1).rstrip("1")
    ws.update(range_name=f"{col}2:{col}{len(vals)}",values=out,value_input_option="RAW")

report={
  "rows":len(vals)-1,
  "category_tree_ids":len(names),
  "category_names_written":written,
  "unknown_category_ids":unknown
}
with open("catalog/ozon_categories_report.json","w",encoding="utf-8") as f:
    json.dump(report,f,ensure_ascii=False,indent=2); f.write("\n")
print(json.dumps(report,ensure_ascii=False,indent=2))
