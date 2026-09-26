#!/usr/bin/env python3
from __future__ import annotations
import json, math, os, time
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import gspread, requests
from google.oauth2.service_account import Credentials

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/"catalog"/"norden_price_retry_kit_yandex_report.json"
SHEET_ID=os.environ.get("CATALOG_SPREADSHEET_ID","1DvMYvKRr8fX8mfLFaeJCXCccQofPViPcIP2CgIctc8w")
SHEET_NAME=os.environ.get("CATALOG_SHEET","Норден")
KIT="https://api.kit.yandex.net"
YA="https://api.partner.market.yandex.ru"

def s(v): return str(v or "").strip()
def now(): return datetime.now(timezone.utc).isoformat()
def money(v):
    if v in (None,""): return None
    try: d=Decimal(str(v).replace("\xa0","").replace(" ","").replace(",","."))
    except (InvalidOperation,ValueError,TypeError): return None
    return d if d>0 else None
def mstr(v):
    d=money(v)
    return None if d is None else f"{d.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP):.2f}"
def ceilr(v):
    d=money(v); return None if d is None else int(math.ceil(float(d)-1e-9))
def chunks(x,n):
    for i in range(0,len(x),n): yield x[i:i+n]

def req(session,method,url,headers,body=None,params=None):
    for attempt in range(6):
        r=session.request(method,url,headers=headers,json=body,params=params,timeout=120)
        if r.status_code==429 or r.status_code>=500:
            if attempt<5:
                time.sleep(min(15,2**attempt)); continue
        if r.status_code>=400: raise RuntimeError(f"HTTP {r.status_code}: {r.text[:1200]}")
        return r.json() if r.content else {}
    raise RuntimeError("retries exhausted")

def load_rows():
    creds=Credentials.from_service_account_info(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"])
    ws=gspread.authorize(creds).open_by_key(SHEET_ID).worksheet(SHEET_NAME)
    vals=ws.get_all_values(); h=vals[0]; ix={x:i for i,x in enumerate(h)}
    need=["Артикул","YML ID","KIT Цена до скидки","KIT Цена со скидкой",
          "Yandex цена","Yandex зачёркнутая цена","Yandex offer_id","Yandex business_id"]
    miss=[x for x in need if x not in ix]
    if miss: raise RuntimeError("missing columns: "+", ".join(miss))
    out=[]
    for rn,row in enumerate(vals[1:],2):
        def g(k): return row[ix[k]] if ix[k]<len(row) else ""
        art=s(g("Артикул"))
        if not art: continue
        out.append({"row":rn,"article":art,"yml":s(g("YML ID")),
                    "kit_old":money(g("KIT Цена до скидки")),"kit_sale":money(g("KIT Цена со скидкой")),
                    "ya_sale":money(g("Yandex цена")),"ya_old":money(g("Yandex зачёркнутая цена")),
                    "ya_offer":s(g("Yandex offer_id")),"ya_business":s(g("Yandex business_id"))})
    return out

def sync_kit(rows,rep):
    p=rep["KIT"]; sess=requests.Session()
    hdr={"Authorization":f"Bearer {os.environ['YANDEX_KIT_TOKEN'].strip()}","Accept":"application/json","Content-Type":"application/json"}
    mp=json.loads((ROOT/"norden-kit"/"kit_mapping.json").read_text(encoding="utf-8"))
    bysku=defaultdict(list); bysup=defaultdict(list)
    for supplier,vs in (mp.get("variants") or {}).items():
        for v in vs or []:
            vid=s(v.get("variant_id")); sku=s(v.get("sku"))
            if not vid: continue
            bysup[s(supplier)].append(vid)
            if sku: bysku[sku].append(vid)
    targets=[]; seen=set(); missing=[]
    for r in rows:
        if not(r["kit_old"] and r["kit_sale"]): continue
        vids=bysku.get(r["article"]) or bysup.get(r["yml"]) or []
        if not vids: missing.append(r["article"]); continue
        for vid in vids:
            if vid in seen: continue
            seen.add(vid)
            targets.append({"article":r["article"],"variant_id":vid,"price":mstr(r["kit_old"]),"manual_discount_price":mstr(r["kit_sale"])})
    p["targeted"]=len(targets); p["missing_mapping"]=len(missing); p["missing_sample"]=missing[:50]
    updated=0; errors=[]
    for batch in chunks(targets,500):
        items=[{"variant_id":x["variant_id"],"price":x["price"],"manual_discount_price":x["manual_discount_price"]} for x in batch]
        try:
            req(sess,"POST",KIT+"/v1/variants/prices/bulk_update",hdr,{"items":items})
            updated+=len(batch)
        except Exception:
            for x,it in zip(batch,items):
                try:
                    req(sess,"POST",KIT+"/v1/variants/prices/bulk_update",hdr,{"items":[it]}); updated+=1
                except Exception as e:
                    errors.append({"article":x["article"],"variant_id":x["variant_id"],"error":str(e)[:500]})
    p["updated"]=updated; p["errors"]=errors[:50]; p["api_error_count"]=len(errors)
    verified=0; mism=[]
    for x in targets[:25]:
        try:
            v=req(sess,"GET",KIT+f"/v1/variants/{x['variant_id']}",hdr)
            old=money(v.get("price") or (v.get("pricing") or {}).get("price"))
            sale=money(v.get("manual_discount_price") or (v.get("pricing") or {}).get("manual_discount_price"))
            if old==money(x["price"]) and sale==money(x["manual_discount_price"]): verified+=1
            else: mism.append({"article":x["article"],"actual":[str(old),str(sale)],"expected":[x["price"],x["manual_discount_price"]]})
        except Exception as e: mism.append({"article":x["article"],"error":str(e)[:500]})
    p["verified_sample"]=verified; p["verification_sample_size"]=min(25,len(targets)); p["verification_mismatch"]=mism[:25]
    p["minimum_price_note"]="KIT API does not support a separate minimum-price field; internal KIT Минимальная цена was not pushed."
    p["status"]="УСПЕШНО" if updated==len(targets) and not mism else "ЧАСТИЧНО"

def sync_yandex(rows,rep):
    p=rep["Yandex"]; sess=requests.Session()
    hdr={"Api-Key":os.environ["YANDEX_MARKET_API_KEY"].strip(),"Accept":"application/json","Content-Type":"application/json"}
    groups=defaultdict(list)
    for r in rows:
        if not(r["ya_business"] and r["ya_offer"] and r["ya_sale"] and r["ya_old"]): continue
        sale=ceilr(r["ya_sale"]); old=ceilr(r["ya_old"])
        if old<=sale: continue
        groups[r["ya_business"]].append({"offerId":r["ya_offer"],"price":{"value":sale,"currencyId":"RUR","discountBase":old}})
    p["targeted"]=sum(len(v) for v in groups.values())
    accepted=0; errors=[]
    for bid,items in groups.items():
        for batch in chunks(items,200):
            try:
                d=req(sess,"POST",YA+f"/v2/businesses/{bid}/offer-prices/updates",hdr,{"offers":batch})
                if s(d.get("status")).upper()=="OK" and not d.get("errors"): accepted+=len(batch)
                else: errors.append({"business_id":bid,"response":d})
            except Exception as e: errors.append({"business_id":bid,"error":str(e)[:700]})
    p["accepted"]=accepted; p["errors"]=errors[:50]; p["api_error_count"]=len(errors)
    time.sleep(5)
    verified=0; mism=[]
    for bid,items in groups.items():
        exp={x["offerId"]:x["price"] for x in items}
        for ids in chunks(list(exp),200):
            try:
                d=req(sess,"POST",YA+f"/v2/businesses/{bid}/offer-prices",hdr,{"offerIds":ids},params={"limit":500})
                for off in ((d.get("result") or {}).get("offers") or []):
                    oid=s(off.get("offerId")); pr=off.get("price") or {}
                    if oid not in exp: continue
                    if ceilr(pr.get("value"))==int(exp[oid]["value"]) and ceilr(pr.get("discountBase"))==int(exp[oid]["discountBase"]):
                        verified+=1
                    elif len(mism)<50:
                        mism.append({"offer_id":oid,"actual":[pr.get("value"),pr.get("discountBase")],"expected":[exp[oid]["value"],exp[oid]["discountBase"]]})
            except Exception as e:
                mism.append({"business_id":bid,"error":str(e)[:700]})
    p["verified"]=verified; p["verification_mismatch"]=mism[:50]
    p["status"]="УСПЕШНО" if accepted==p["targeted"] and verified==p["targeted"] and not errors else ("ЧАСТИЧНО" if accepted else "ОШИБКА")

def main():
    rep={"started_at":now(),"source":{"spreadsheet_id":SHEET_ID,"sheet":SHEET_NAME},"KIT":{},"Yandex":{}}
    try:
        rows=load_rows(); rep["catalog_rows"]=len(rows)
        try: sync_kit(rows,rep)
        except Exception as e: rep["KIT"]={"status":"ОШИБКА","errors":[str(e)[:2000]]}
        try: sync_yandex(rows,rep)
        except Exception as e: rep["Yandex"]={"status":"ОШИБКА","errors":[str(e)[:2000]]}
        rep["status"]="УСПЕШНО" if rep["KIT"].get("status")=="УСПЕШНО" and rep["Yandex"].get("status")=="УСПЕШНО" else "ЗАВЕРШЕНО С ОШИБКАМИ"
    except Exception as e:
        rep["status"]="ОШИБКА"; rep["fatal_error"]=str(e)[:2500]
    rep["finished_at"]=now()
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(rep,ensure_ascii=False,indent=2))
    raise SystemExit(0 if rep["status"]=="УСПЕШНО" else 1)

if __name__=="__main__": main()
