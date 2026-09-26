#!/usr/bin/env python3
from __future__ import annotations
import json, os, sys
from pathlib import Path
import gspread
from google.oauth2.service_account import Credentials
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"webasyst"))
from client import WebasystClient

SID=os.environ.get("CATALOG_SPREADSHEET_ID","1DvMYvKRr8fX8mfLFaeJCXCccQofPViPcIP2CgIctc8w")
SHEET=os.environ.get("CATALOG_SHEET","Норден")
OUT=ROOT/"catalog"/"webasyst_kit_id_sync_report.json"
TARGETS={x.strip() for x in os.environ.get("TARGET_ARTICLES","").split(",") if x.strip()}

def s(v): return str(v or "").strip()
creds=json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
gc=gspread.authorize(Credentials.from_service_account_info(creds,scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]))
ws=gc.open_by_key(SID).worksheet(SHEET)
vals=ws.get_all_values()
h=vals[0]; ix={x:i for i,x in enumerate(h)}
for col in ("Артикул","Webasyst product_id","KIT ID"):
    if col not in ix: raise RuntimeError(f"Missing column {col}")
rows=[]
for rn,row in enumerate(vals[1:],2):
    art=s(row[ix["Артикул"]] if ix["Артикул"]<len(row) else "")
    if not art or (TARGETS and art not in TARGETS): continue
    pid=s(row[ix["Webasyst product_id"]] if ix["Webasyst product_id"]<len(row) else "")
    kid=s(row[ix["KIT ID"]] if ix["KIT ID"]<len(row) else "")
    if pid and kid and kid.isdigit(): rows.append((rn,art,pid,kid))
wa=WebasystClient(min_request_interval=0.45)
report={"status":"УСПЕШНО","targeted":len(rows),"updated":0,"unchanged":0,"errors":[],"items":[]}
for rn,art,pid,kid in rows:
    try:
        before=wa.call("shop.product.getInfo",params={"id":pid})
        bf=before.get("features") or {}
        current=s(bf.get("kit_id") if isinstance(bf,dict) else "")
        if current!=kid:
            wa.call("shop.product.update",http_method="POST",params={"id":pid},data={"features":{"kit_id":kid}})
        after=wa.call("shop.product.getInfo",params={"id":pid})
        af=after.get("features") or {}
        got=s(af.get("kit_id") if isinstance(af,dict) else "")
        if got!=kid: raise RuntimeError(f"KIT ID readback mismatch {got!r} != {kid!r}")
        # Safety: product update must not wipe unrelated existing feature values.
        if isinstance(bf,dict) and isinstance(af,dict):
            lost=[k for k,v in bf.items() if k!="kit_id" and v not in (None,"",[]) and af.get(k) in (None,"",[])]
            if lost: raise RuntimeError("Unrelated Webasyst features were lost: "+", ".join(lost[:20]))
        if current==kid: report["unchanged"]+=1
        else: report["updated"]+=1
        report["items"].append({"article":art,"product_id":pid,"kit_id":kid,"before":current,"after":got})
    except Exception as e:
        report["errors"].append({"article":art,"product_id":pid,"kit_id":kid,"error":str(e)[:1200]})
if report["errors"]: report["status"]="ЗАВЕРШЕНО С ОШИБКАМИ"
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(report,ensure_ascii=False))
raise SystemExit(0 if not report["errors"] else 2)
