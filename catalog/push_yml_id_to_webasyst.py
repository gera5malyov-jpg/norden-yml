#!/usr/bin/env python3
from __future__ import annotations
import json, os, re, sys, unicodedata
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"webasyst"))
from client import WebasystClient

SPREADSHEET_ID=os.environ["CATALOG_SPREADSHEET_ID"].strip()
SHEET_NAME=os.environ.get("CATALOG_SHEET","Норден").strip()
SA_JSON=os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
TYPE_NAME="NORDEN-100"
REPORT=ROOT/"catalog"/"webasyst_yml_id_backfill_report.json"

def s(v): return str(v or "").strip()
def norm(v): return unicodedata.normalize("NFKC",s(v)).casefold()

def listify(payload, keys=()):
    if isinstance(payload,list): return [x for x in payload if isinstance(x,dict)]
    if not isinstance(payload,dict): return []
    for k in keys:
        v=payload.get(k)
        if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
        if isinstance(v,dict): return [x for x in v.values() if isinstance(x,dict)]
    if payload and all(isinstance(v,dict) for v in payload.values()): return list(payload.values())
    return []

def skus(product):
    v=product.get("skus")
    if isinstance(v,dict): return [x for x in v.values() if isinstance(x,dict)]
    if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
    return []

def load_products(wa,type_id):
    out=[]; offset=0
    while True:
        d=wa.call("shop.product.search",params={
            "hash":f"type/{type_id}","offset":offset,"limit":1000,"fields":"*,skus"
        })
        batch=listify(d,("products","items"))
        out.extend(batch)
        total=(d.get("count") or d.get("total_count")) if isinstance(d,dict) else None
        if not batch or len(batch)<1000: break
        if total not in (None,"") and len(out)>=int(total): break
        offset += len(batch)
    return out

creds=json.loads(SA_JSON)
gc=gspread.authorize(Credentials.from_service_account_info(
    creds,scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
))
ws=gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
vals=ws.get_all_values()
headers=vals[0]
ai=headers.index("Артикул"); yi=headers.index("YML ID")

catalog={}
duplicates=[]
for rn,row in enumerate(vals[1:],start=2):
    article=s(row[ai] if ai<len(row) else "")
    yml_id=s(row[yi] if yi<len(row) else "")
    if not article or not yml_id: continue
    k=norm(article)
    if k in catalog:
        duplicates.append(article)
        continue
    catalog[k]={"article":article,"yml_id":yml_id,"row":rn}

wa=WebasystClient(min_request_interval=0.45)
types=listify(wa.call("shop.type.getList"))
matches=[x for x in types if norm(x.get("name") or x.get("title"))==norm(TYPE_NAME)]
if len(matches)!=1: raise RuntimeError(f"Expected one type {TYPE_NAME}, found {len(matches)}")
type_id=s(matches[0].get("id"))
products=load_products(wa,type_id)

by_sku={}
dup_wa=[]
for p in products:
    pid=s(p.get("id"))
    for sku in skus(p):
        article=s(sku.get("sku"))
        if not article: continue
        k=norm(article)
        if k in by_sku: dup_wa.append(article)
        else: by_sku[k]={"product":p,"product_id":pid,"sku_id":s(sku.get("id")),"article":article}

targets=[]
missing_in_wa=[]
for k,row in catalog.items():
    w=by_sku.get(k)
    if not w:
        missing_in_wa.append(row)
        continue
    targets.append((row,w))

report={
    "ok":False,"type_id":type_id,"catalog_with_yml_id":len(catalog),
    "webasyst_products":len(products),"webasyst_skus":len(by_sku),
    "matched_exact_article":len(targets),"missing_in_webasyst":len(missing_in_wa),
    "catalog_duplicate_articles":len(duplicates),"webasyst_duplicate_skus":len(dup_wa),
    "updated":0,"unchanged":0,"errors":[],"smoke_test":None
}

# Smoke test: update first matched product with its intended yml_id only, then re-read.
if targets:
    row,w=targets[0]
    pid=w["product_id"]
    before=wa.call("shop.product.getInfo",params={"id":pid})
    before_yml=s(before.get("yml_id") if isinstance(before,dict) else "")
    wa.call("shop.product.update",http_method="POST",params={"id":pid},data={"yml_id":row["yml_id"]})
    after=wa.call("shop.product.getInfo",params={"id":pid})
    after_yml=s(after.get("yml_id") if isinstance(after,dict) else "")
    if after_yml != row["yml_id"]:
        raise RuntimeError(f"Smoke test failed: expected yml_id {row['yml_id']!r}, got {after_yml!r}")
    report["smoke_test"]={"article":row["article"],"product_id":pid,"before":before_yml,"after":after_yml}

for row,w in targets:
    try:
        pid=w["product_id"]
        current=s(w["product"].get("yml_id"))
        if current==row["yml_id"]:
            report["unchanged"]+=1
            continue
        wa.call("shop.product.update",http_method="POST",params={"id":pid},data={"yml_id":row["yml_id"]})
        check=wa.call("shop.product.getInfo",params={"id":pid})
        got=s(check.get("yml_id") if isinstance(check,dict) else "")
        if got!=row["yml_id"]:
            raise RuntimeError(f"verification failed: {got!r}")
        report["updated"]+=1
    except Exception as exc:
        report["errors"].append({"article":row["article"],"product_id":w["product_id"],"error":str(exc)[:1000]})

report["ok"]=not report["errors"]
report["missing_in_webasyst_sample"]=missing_in_wa[:100]
REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(report,ensure_ascii=False))
if report["errors"]: raise SystemExit(2)
