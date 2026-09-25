#!/usr/bin/env python3
import json, os, time, requests, gspread
from google.oauth2.service_account import Credentials

BID=os.environ.get("YANDEX_MARKET_BUSINESS_ID","20806099")
TOKEN=os.environ["YANDEX_MARKET_API_KEY"]
SA=os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
SID=os.environ["CATALOG_SPREADSHEET_ID"]
SHEET=os.environ.get("CATALOG_SHEET","Норден")
BASE="https://api.partner.market.yandex.ru"
TARGETS={
"AF-31383356":10785222,
"AF-30539744":61276996,
"AF-31390579":10785222,
"AF-31646769":10785222,
}
def call(ids):
    r=requests.post(f"{BASE}/v2/businesses/{BID}/offer-mappings",
      headers={"Api-Key":TOKEN,"Content-Type":"application/json","Accept":"application/json"},
      json={"offerIds":ids},timeout=60)
    r.raise_for_status()
    out={}
    for x in ((r.json().get("result") or {}).get("offerMappings") or []):
        off=x.get("offer") or {}; mp=x.get("mapping") or {}
        oid=str(off.get("offerId") or "").strip()
        if oid:
            out[oid]={
              "id":int(mp.get("marketCategoryId") or off.get("marketCategoryId") or 0),
              "name":str(mp.get("marketCategoryName") or off.get("marketCategoryName") or "").strip()
            }
    return out

verified={}
last={}
for attempt in range(16):
    last=call(list(TARGETS))
    verified={oid:last.get(oid) for oid,cid in TARGETS.items() if last.get(oid) and last[oid]["id"]==cid}
    if len(verified)==len(TARGETS): break
    if attempt<15: time.sleep(15)

info=json.loads(SA)
cr=Credentials.from_service_account_info(info,scopes=[
 "https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"])
ws=gspread.authorize(cr).open_by_key(SID).worksheet(SHEET)
vals=ws.get_all_values(); h=vals[0]; idx={x:i for i,x in enumerate(h)}
if verified and "Yandex category_id" in idx:
    updates=[]
    for rn,row in enumerate(vals[1:],2):
        art=str(row[idx["Артикул"]] if idx["Артикул"]<len(row) else "").strip()
        if art in verified:
            updates.append({"range":gspread.utils.rowcol_to_a1(rn,idx["Yandex category_id"]+1),"values":[[verified[art]["id"]]]})
    if updates: ws.batch_update(updates,value_input_option="RAW")
report={"targets":TARGETS,"verified":verified,"last_readback":last,
        "verified_count":len(verified),"pending_count":len(TARGETS)-len(verified)}
with open("catalog/yandex_category_verify_report.json","w",encoding="utf-8") as f:
    json.dump(report,f,ensure_ascii=False,indent=2); f.write("\n")
print(json.dumps(report,ensure_ascii=False,indent=2))
