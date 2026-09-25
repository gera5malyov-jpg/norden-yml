#!/usr/bin/env python3
from __future__ import annotations
import json, os, re, time
from collections import defaultdict
from datetime import datetime, timezone

import requests, gspread
from google.oauth2.service_account import Credentials

SID=os.environ["CATALOG_SPREADSHEET_ID"].strip()
SHEET=os.environ.get("CATALOG_SHEET","Норден").strip()
BID=os.environ.get("YANDEX_MARKET_BUSINESS_ID","20806099").strip()
TOKEN=os.environ["YANDEX_MARKET_API_KEY"].strip()
SA=os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
BASE="https://api.partner.market.yandex.ru"
NOW=datetime.now(timezone.utc).isoformat()

def s(v): return str(v or "").strip()
def norm(v): return re.sub(r"\s+"," ",s(v)).casefold()
def chunks(a,n):
    for i in range(0,len(a),n): yield a[i:i+n]

def call(method,path,body=None,retries=8):
    h={"Api-Key":TOKEN,"Content-Type":"application/json","Accept":"application/json"}
    last=None
    for attempt in range(retries):
        r=requests.request(method,BASE+path,headers=h,json=body,timeout=90)
        last=r
        if r.status_code in (420,429) or r.status_code>=500:
            time.sleep(float(r.headers.get("Retry-After") or min(30,2**attempt)))
            continue
        try: data=r.json() if r.content else {}
        except Exception: data={"raw":r.text}
        if r.status_code>=400:
            raise RuntimeError(f"HTTP {r.status_code} {path}: {json.dumps(data,ensure_ascii=False)[:2000]}")
        return data
    raise RuntimeError(f"request failed {path}: {last.status_code if last else 'N/A'}")

def open_sheet():
    info=json.loads(SA)
    cr=Credentials.from_service_account_info(info,scopes=[
      "https://www.googleapis.com/auth/spreadsheets",
      "https://www.googleapis.com/auth/drive"
    ])
    return gspread.authorize(cr).open_by_key(SID).worksheet(SHEET)

def get_mappings(offer_ids):
    out={}
    for batch in chunks(offer_ids,100):
        d=call("POST",f"/v2/businesses/{BID}/offer-mappings",{"offerIds":batch})
        for x in ((d.get("result") or {}).get("offerMappings") or []):
            off=x.get("offer") or {}
            mp=x.get("mapping") or {}
            oid=s(off.get("offerId"))
            if not oid: continue
            out[oid]={
              "category_id": int(mp.get("marketCategoryId") or off.get("marketCategoryId") or 0),
              "category_name": s(mp.get("marketCategoryName") or off.get("marketCategoryName")),
              "raw": x,
            }
    return out

def category_index():
    d=call("POST","/v2/categories/tree",{"language":"RU"})
    root=d.get("result") or {}
    by_name=defaultdict(list)
    by_id={}
    def walk(node,path):
        if not isinstance(node,dict): return
        cid=int(node.get("id") or 0)
        name=s(node.get("name"))
        children=[x for x in (node.get("children") or []) if isinstance(x,dict)]
        new_path=path+[name] if name else path
        if cid:
            by_id[cid]={"id":cid,"name":name,"path":" > ".join([x for x in new_path if x]),"leaf":not children}
            if name and not children:
                by_name[norm(name)].append(by_id[cid])
        for child in children:
            walk(child,new_path)
    walk(root,[])
    return by_name,by_id

ws=open_sheet()
vals=ws.get_all_values()
headers=[s(x) for x in vals[0]]
idx={h:i for i,h in enumerate(headers)}
required=["Артикул","Yandex категория","Yandex category_id"]
missing=[x for x in required if x not in idx]
if missing:
    raise RuntimeError("Нет колонок: "+", ".join(missing))

rows=[]
for rn,row in enumerate(vals[1:],2):
    art=s(row[idx["Артикул"]] if idx["Артикул"]<len(row) else "")
    desired=s(row[idx["Yandex категория"]] if idx["Yandex категория"]<len(row) else "")
    if art and desired:
        rows.append({"row":rn,"offer_id":art,"desired_name":desired})

offer_ids=sorted({x["offer_id"] for x in rows})
current=get_mappings(offer_ids)
by_name,by_id=category_index()

changes=[]
unresolved=[]
unchanged=0
not_found=0
for x in rows:
    cur=current.get(x["offer_id"])
    if not cur:
        not_found+=1
        continue
    if norm(cur["category_name"])==norm(x["desired_name"]):
        unchanged+=1
        continue
    matches=by_name.get(norm(x["desired_name"])) or []
    if len(matches)!=1:
        unresolved.append({
          "offer_id":x["offer_id"],"desired_name":x["desired_name"],
          "current_name":cur["category_name"],"matches":matches
        })
        continue
    target=matches[0]
    changes.append({
      "row":x["row"],"offer_id":x["offer_id"],
      "from_id":cur["category_id"],"from_name":cur["category_name"],
      "to_id":target["id"],"to_name":target["name"],"to_path":target["path"]
    })

accepted=[]
api_errors=[]
api_warnings=[]
for batch in chunks(changes,100):
    body={"offerMappings":[{"offer":{"offerId":x["offer_id"],"marketCategoryId":x["to_id"]}} for x in batch]}
    d=call("POST",f"/v2/businesses/{BID}/offer-mappings/update",body)
    results=d.get("results") or []
    result_by={s(r.get("offerId")):r for r in results if isinstance(r,dict)}
    for x in batch:
        rr=result_by.get(x["offer_id"]) or {}
        errs=rr.get("errors") or []
        warns=rr.get("warnings") or []
        if errs:
            api_errors.append({**x,"errors":errs,"warnings":warns})
        else:
            accepted.append(x)
            if warns:
                api_warnings.append({**x,"warnings":warns})

# Каталог обновляется не мгновенно; несколько коротких read-back попыток.
verified={}
pending={x["offer_id"]:x for x in accepted}
for attempt in range(6):
    if not pending: break
    if attempt: time.sleep(10)
    fresh=get_mappings(sorted(pending))
    done=[]
    for oid,x in pending.items():
        cur=fresh.get(oid)
        if cur and int(cur["category_id"] or 0)==int(x["to_id"]):
            verified[oid]={"category_id":cur["category_id"],"category_name":cur["category_name"]}
            done.append(oid)
    for oid in done: pending.pop(oid,None)

# В таблице category_id меняем только после подтверждения read-back.
if verified:
    id_col=idx["Yandex category_id"]+1
    updates=[]
    for x in accepted:
        if x["offer_id"] in verified:
            updates.append({
              "range":gspread.utils.rowcol_to_a1(x["row"],id_col),
              "values":[[verified[x["offer_id"]]["category_id"]]]
            })
    for batch in chunks(updates,200):
        ws.batch_update(batch,value_input_option="RAW")

report={
  "started_at":NOW,
  "business_id":BID,
  "sheet_rows_with_category":len(rows),
  "current_mappings_found":len(current),
  "unchanged":unchanged,
  "not_found_in_yandex":not_found,
  "requested_changes":len(changes),
  "accepted_by_api":len(accepted),
  "verified_changed":len(verified),
  "pending_readback":len(pending),
  "unresolved_or_ambiguous":len(unresolved),
  "api_errors":len(api_errors),
  "api_warnings":len(api_warnings),
  "changes":changes,
  "unresolved":unresolved,
  "errors":api_errors,
  "warnings":api_warnings,
  "pending":[pending[k] for k in sorted(pending)],
  "finished_at":datetime.now(timezone.utc).isoformat()
}
with open("catalog/yandex_category_apply_report.json","w",encoding="utf-8") as f:
    json.dump(report,f,ensure_ascii=False,indent=2); f.write("\n")
print(json.dumps({k:v for k,v in report.items() if k not in ("changes","unresolved","errors","warnings","pending")},ensure_ascii=False,indent=2))
