#!/usr/bin/env python3
from __future__ import annotations
import json, os, re, time, unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
import gspread, requests
from google.oauth2.service_account import Credentials

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/"catalog"/"norden_price_retry_kit_targeted_report.json"
MAPPING=ROOT/"norden-kit"/"kit_mapping.json"
SHEET_ID=os.environ.get("CATALOG_SPREADSHEET_ID","1DvMYvKRr8fX8mfLFaeJCXCccQofPViPcIP2CgIctc8w")
SHEET_NAME=os.environ.get("CATALOG_SHEET","Норден")
BASE="https://api.kit.yandex.net"
CONFUSABLES=str.maketrans({"а":"a","в":"b","с":"c","е":"e","н":"h","к":"k","м":"m","о":"o","р":"p","т":"t","х":"x","у":"y","А":"a","В":"b","С":"c","Е":"e","Н":"h","К":"k","М":"m","О":"o","Р":"p","Т":"t","Х":"x","У":"y"})

def now(): return datetime.now(timezone.utc).isoformat()
def s(v): return str(v or "").strip()
def norm(v):
    return re.sub(r"[^0-9a-z]+","",unicodedata.normalize("NFKC",s(v)).translate(CONFUSABLES).casefold())
def dec(v):
    try: return Decimal(str(v).replace(" ","").replace(",","."))
    except (InvalidOperation,ValueError,TypeError): return None
def ceilrub(v):
    d=dec(v)
    return None if d is None or d<=0 else int(d.quantize(Decimal("1"),rounding=ROUND_CEILING))

class API:
    def __init__(self):
        self.h={"Authorization":f"Bearer {s(os.environ['YANDEX_KIT_TOKEN'])}","Accept":"application/json","Content-Type":"application/json"}
        self.s=requests.Session()
    def req(self,method,path,params=None,body=None):
        for a in range(10):
            r=self.s.request(method,BASE+path,headers=self.h,params=params,json=body,timeout=120)
            if r.status_code==429:
                time.sleep(min(20,2+a*2)); continue
            if r.status_code>=500:
                time.sleep(min(20,2**a)); continue
            if r.status_code>=400: raise RuntimeError(f"HTTP {r.status_code}: {r.text[:1200]}")
            return r.json() if r.content else {}
        raise RuntimeError("KIT retries exhausted")
    @staticmethod
    def items(p):
        if isinstance(p,list): return p
        if isinstance(p,dict):
            for k in ("items","results","variants","data"):
                v=p.get(k)
                if isinstance(v,list): return v
                if isinstance(v,dict) and isinstance(v.get("items"),list): return v["items"]
        return []

def load_rows():
    creds=Credentials.from_service_account_info(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"])
    vals=gspread.authorize(creds).open_by_key(SHEET_ID).worksheet(SHEET_NAME).get_all_values()
    h=vals[0]; ix={x:i for i,x in enumerate(h)}
    need=["Артикул","YML ID","KIT Цена до скидки","KIT Цена со скидкой"]
    rows=[]
    for row in vals[1:]:
        def g(k): return row[ix[k]] if ix[k]<len(row) else ""
        art=s(g("Артикул")); old=ceilrub(g("KIT Цена до скидки")); sale=ceilrub(g("KIT Цена со скидкой"))
        if art and old and sale: rows.append({"article":art,"yml":s(g("YML ID")),"old":old,"sale":sale})
    return rows

def all_values(obj):
    out=[]
    def walk(x):
        if isinstance(x,dict):
            for k,v in x.items():
                if k in ("value","values","sku","code","article","external_id") and not isinstance(v,(dict,list)): out.append(s(v))
                walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
    walk(obj)
    return [v for v in out if v]

def main():
    rep={"started_at":now(),"source":{"spreadsheet_id":SHEET_ID,"sheet":SHEET_NAME}}
    try:
        rows=load_rows(); by={r["article"]:r for r in rows}; rep["catalog_rows"]=len(rows)
        mapping=json.loads(MAPPING.read_text(encoding="utf-8")) if MAPPING.exists() else {"variants":{}}
        api=API()
        resolved={}; unresolved=[]
        # Keep valid existing mappings; rescue only missing/stale mappings.
        def validate_existing(r):
            for m in (mapping.get("variants",{}).get(r["article"]) or []):
                vid=s(m.get("variant_id"))
                if not vid: continue
                try:
                    api.req("GET",f"/v1/variants/{vid}")
                    return r["article"],vid,"mapping"
                except Exception:
                    pass
            return r["article"],None,"missing"
        with ThreadPoolExecutor(max_workers=8) as pool:
            fs=[pool.submit(validate_existing,r) for r in rows]
            for f in as_completed(fs):
                art,vid,method=f.result()
                if vid: resolved[art]={"variant_id":vid,"method":method}
                else: unresolved.append(by[art])
        rep["valid_existing_mapping"]=len(resolved); rep["needs_targeted_lookup"]=len(unresolved)

        def lookup(r):
            keys=[r["article"]]+([r["yml"]] if r["yml"] else [])
            candidates={}
            for q in keys:
                try:
                    rows2=api.items(api.req("GET","/v1/variants",params={"name":q,"page":1,"per_page":100}))
                except Exception as e:
                    continue
                for v in rows2:
                    if s(v.get("brand")).casefold()!="norden": continue
                    if s(v.get("status")).upper()=="ARCHIVED": continue
                    vals=[s(v.get("sku"))]+all_values(v.get("characteristics") or [])
                    if any(norm(x) in {norm(r["article"]),norm(r["yml"])} for x in vals if norm(x)):
                        vid=s(v.get("id"))
                        if vid: candidates[vid]=v
                if len(candidates)==1: break
            if len(candidates)==1:
                vid=next(iter(candidates))
                return r["article"],vid,None
            return r["article"],None,("not_found" if not candidates else f"ambiguous:{len(candidates)}")
        with ThreadPoolExecutor(max_workers=8) as pool:
            fs=[pool.submit(lookup,r) for r in unresolved]
            done=0
            for f in as_completed(fs):
                art,vid,err=f.result(); done+=1
                if vid: resolved[art]={"variant_id":vid,"method":"targeted_live_lookup"}
                elif err: rep.setdefault("lookup_errors",[]).append({"article":art,"error":err})
                if done%50==0: print(f"KIT targeted lookup {done}/{len(unresolved)}",flush=True)
        rep["resolved_total"]=len(resolved); rep["targeted_lookup_resolved"]=sum(1 for x in resolved.values() if x["method"]=="targeted_live_lookup")
        missing=[a for a in by if a not in resolved]; rep["missing_live_match"]=len(missing); rep["missing_sample"]=missing[:100]

        items=[{"article":a,"variant_id":resolved[a]["variant_id"],"old":by[a]["old"],"sale":by[a]["sale"]} for a in resolved]
        updated=0; stale=[]; errors=[]
        for i in range(0,len(items),500):
            pending=items[i:i+500]
            while pending:
                try:
                    api.req("POST","/v1/variants/prices/bulk_update",body={"items":[{"variant_id":x["variant_id"],"price":str(x["old"]),"manual_discount_price":str(x["sale"])} for x in pending]})
                    updated+=len(pending); break
                except Exception as e:
                    msg=str(e)
                    try:
                        p=json.loads(msg.split(": ",1)[1])
                        bad={s(x.get("variant_id")) for x in p.get("errors",[]) if s(x.get("code"))=="VARIANT_NOT_FOUND"}
                    except Exception: bad=set()
                    if not bad: errors.append({"error":msg[:1200],"batch_size":len(pending)}); break
                    stale.extend(bad); pending=[x for x in pending if x["variant_id"] not in bad]
        rep["updated"]=updated; rep["stale_variant_ids"]=list(dict.fromkeys(stale)); rep["api_errors"]=errors

        stale_set=set(stale)
        def verify(x):
            if x["variant_id"] in stale_set: return x["article"],False,"stale"
            try:
                v=api.req("GET",f"/v1/variants/{x['variant_id']}")
                old=ceilrub(v.get("price") or (v.get("pricing") or {}).get("price"))
                sale=ceilrub(v.get("manual_discount_price") or (v.get("pricing") or {}).get("manual_discount_price"))
                return x["article"],old==x["old"] and sale==x["sale"],[old,sale,x["old"],x["sale"]]
            except Exception as e: return x["article"],False,str(e)[:500]
        verified=0; mism=[]
        with ThreadPoolExecutor(max_workers=8) as pool:
            fs=[pool.submit(verify,x) for x in items]
            for f in as_completed(fs):
                art,ok,info=f.result()
                if ok: verified+=1
                elif len(mism)<100: mism.append({"article":art,"detail":info})
        rep["verified"]=verified; rep["verification_mismatch"]=mism
        rep["minimum_price_note"]="KIT API has no supported separate minimum-price field; KIT Минимальная цена remains internal."
        rep["status"]="УСПЕШНО" if updated==len(items) and verified==len(items) and not errors and not stale else "ЧАСТИЧНО"
    except Exception as e:
        rep["status"]="ОШИБКА"; rep["fatal_error"]=str(e)[:2500]
    rep["finished_at"]=now()
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(rep,ensure_ascii=False,indent=2))
    raise SystemExit(0 if rep["status"]=="УСПЕШНО" else 1)

if __name__=="__main__": main()
