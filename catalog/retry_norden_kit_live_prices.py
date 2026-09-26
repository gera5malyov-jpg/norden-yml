#!/usr/bin/env python3
from __future__ import annotations
import json, math, os, re, time, unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path

import gspread, requests
from google.oauth2.service_account import Credentials

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/"catalog"/"norden_price_retry_kit_live_report.json"
SHEET_ID=os.environ.get("CATALOG_SPREADSHEET_ID","1DvMYvKRr8fX8mfLFaeJCXCccQofPViPcIP2CgIctc8w")
SHEET_NAME=os.environ.get("CATALOG_SHEET","Норден")
KIT="https://api.kit.yandex.net"
BRAND="Norden"
IDENTITY_TITLES=("Код для сайта","Артикул","Код продавца","Код Norden","Внешний ID","Внешний идентификатор","External ID")
CONFUSABLES=str.maketrans({
    "а":"a","в":"b","с":"c","е":"e","н":"h","к":"k","м":"m","о":"o","р":"p","т":"t","х":"x","у":"y",
    "А":"a","В":"b","С":"c","Е":"e","Н":"h","К":"k","М":"m","О":"o","Р":"p","Т":"t","Х":"x","У":"y",
})

def now(): return datetime.now(timezone.utc).isoformat()
def s(v): return str(v or "").strip()
def norm(v):
    x=unicodedata.normalize("NFKC",s(v)).translate(CONFUSABLES).casefold()
    return re.sub(r"[^0-9a-z]+","",x)
def money(v):
    if v in (None,""): return None
    try: d=Decimal(str(v).replace("\xa0","").replace(" ","").replace(",","."))
    except (InvalidOperation,ValueError,TypeError): return None
    return d if d>0 else None
def ceilrub(v):
    d=money(v)
    return None if d is None else int(d.quantize(Decimal("1"),rounding=ROUND_CEILING))
def chunks(xs,n):
    for i in range(0,len(xs),n): yield xs[i:i+n]

class Client:
    def __init__(self):
        token=s(os.environ["YANDEX_KIT_TOKEN"])
        self.h={"Authorization":f"Bearer {token}","Accept":"application/json","Content-Type":"application/json"}
        self.s=requests.Session()
    def req(self,method,path,body=None,params=None):
        for attempt in range(10):
            r=self.s.request(method,KIT+path,headers=self.h,json=body,params=params,timeout=120)
            if r.status_code==429 or r.status_code>=500:
                time.sleep(min(20,2**attempt)); continue
            if r.status_code>=400: raise RuntimeError(f"HTTP {r.status_code}: {r.text[:1000]}")
            return r.json() if r.content else {}
        raise RuntimeError("KIT retries exhausted")
    @staticmethod
    def items(p):
        if isinstance(p,list): return p
        if not isinstance(p,dict): return []
        for k in ("items","results","variants","characteristics","products"):
            if isinstance(p.get(k),list): return p[k]
        d=p.get("data")
        if isinstance(d,list): return d
        if isinstance(d,dict) and isinstance(d.get("items"),list): return d["items"]
        return []
    @staticmethod
    def total(p):
        if not isinstance(p,dict): return None
        for k in ("total","total_count"):
            if isinstance(p.get(k),int): return p[k]
        m=p.get("meta")
        if isinstance(m,dict):
            for k in ("total","total_count"):
                if isinstance(m.get(k),int): return m[k]
        return None
    def all_collection(self,path):
        first=self.req("GET",path,params={"page":1,"per_page":100})
        rows=self.items(first); total=self.total(first)
        if total is None or total<=len(rows): return rows
        pages=max(1,math.ceil(total/100)); out=list(rows)
        def one(p): return self.items(self.req("GET",path,params={"page":p,"per_page":100}))
        with ThreadPoolExecutor(max_workers=6) as pool:
            fs=[pool.submit(one,p) for p in range(2,pages+1)]
            for f in as_completed(fs): out.extend(f.result())
        return out

def load_rows():
    creds=Credentials.from_service_account_info(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"])
    vals=gspread.authorize(creds).open_by_key(SHEET_ID).worksheet(SHEET_NAME).get_all_values()
    h=vals[0]; ix={x:i for i,x in enumerate(h)}
    need=["Артикул","YML ID","KIT Цена до скидки","KIT Цена со скидкой"]
    miss=[x for x in need if x not in ix]
    if miss: raise RuntimeError("missing columns: "+", ".join(miss))
    rows=[]
    for rn,row in enumerate(vals[1:],2):
        def g(k): return row[ix[k]] if ix[k]<len(row) else ""
        art=s(g("Артикул")); yml=s(g("YML ID"))
        old=money(g("KIT Цена до скидки")); sale=money(g("KIT Цена со скидкой"))
        if art and old and sale:
            rows.append({"row":rn,"article":art,"yml":yml,"old":old,"sale":sale})
    return rows

def char_value(row,cid):
    for c in row.get("characteristics") or []:
        if s(c.get("characteristic_id"))!=cid: continue
        if s(c.get("value")): return s(c.get("value"))
        vals=c.get("values") or []
        if vals:
            v=vals[0]
            if isinstance(v,dict): return s(v.get("value") or v.get("title") or v.get("name"))
            return s(v)
    return ""

def main():
    rep={"started_at":now(),"source":{"spreadsheet_id":SHEET_ID,"sheet":SHEET_NAME}}
    try:
        rows=load_rows(); rep["catalog_rows"]=len(rows)
        src={}
        idx=defaultdict(set)
        for r in rows:
            src[r["article"]]=r
            for v in (r["article"],r["yml"]):
                k=norm(v)
                if k: idx[k].add(r["article"])
        cli=Client()
        chars=cli.all_collection("/v1/characteristics")
        ids=defaultdict(list)
        wanted={norm(x) for x in IDENTITY_TITLES}
        for c in chars:
            if norm(c.get("title")) in wanted and s(c.get("id")):
                ids[norm(c.get("title"))].append(s(c.get("id")))
        variants=cli.all_collection("/v1/variants")
        rep["kit_variants_scanned"]=len(variants)
        matches=defaultdict(list); unresolved=[]; conflicts=[]
        for v in variants:
            if s(v.get("brand")).casefold()!=BRAND.casefold(): continue
            if s(v.get("status")).upper()=="ARCHIVED": continue
            vals=[]
            if s(v.get("sku")): vals.append(("SKU",s(v.get("sku"))))
            for title in IDENTITY_TITLES:
                for cid in ids.get(norm(title),[]):
                    cv=char_value(v,cid)
                    if cv: vals.append((title,cv))
            cand=defaultdict(list)
            for field,value in vals:
                for art in idx.get(norm(value),set()):
                    cand[art].append({"field":field,"value":value})
            if len(cand)==1:
                art=next(iter(cand))
                vid=s(v.get("id"))
                if vid: matches[art].append({"variant_id":vid,"sku":s(v.get("sku")),"kit_id":v.get("kit_id")})
            elif len(cand)>1:
                if len(conflicts)<100: conflicts.append({"sku":v.get("sku"),"candidates":list(cand)})
            else:
                if len(unresolved)<100: unresolved.append({"sku":v.get("sku"),"kit_id":v.get("kit_id"),"name":v.get("name")})
        rep["mapped_articles"]=len(matches)
        rep["matched_variants"]=sum(len(x) for x in matches.values())
        rep["identity_conflicts"]=len(conflicts)
        rep["unresolved_sample"]=unresolved
        targets=[]; seen=set(); missing=[]
        for r in rows:
            vs=matches.get(r["article"]) or []
            if not vs:
                missing.append(r["article"]); continue
            # Prefer AF-* SKU, then lowest kit_id; update one canonical live variant per article.
            def key(v):
                sku=s(v.get("sku")); kid=v.get("kit_id")
                try: kid=int(kid)
                except Exception: kid=10**18
                return (0 if sku.startswith("AF-") else 1,kid,sku)
            v=sorted(vs,key=key)[0]; vid=v["variant_id"]
            if vid in seen: continue
            seen.add(vid)
            targets.append({"article":r["article"],"variant_id":vid,"old":ceilrub(r["old"]),"sale":ceilrub(r["sale"])})
        rep["targeted"]=len(targets); rep["missing_live_match"]=len(missing); rep["missing_sample"]=missing[:100]
        updated=0; stale=[]; errors=[]
        for batch in chunks(targets,500):
            pending=list(batch)
            while pending:
                body={"items":[{"variant_id":x["variant_id"],"price":str(x["old"]),"manual_discount_price":str(x["sale"])} for x in pending]}
                try:
                    cli.req("POST","/v1/variants/prices/bulk_update",body=body); updated+=len(pending); break
                except Exception as e:
                    msg=str(e)
                    # Parse and drop stale variant ids, retry remaining.
                    try:
                        raw=msg.split(": ",1)[1] if ": " in msg else ""
                        p=json.loads(raw)
                        bad={s(x.get("variant_id")) for x in p.get("errors",[]) if s(x.get("code"))=="VARIANT_NOT_FOUND"}
                    except Exception: bad=set()
                    if not bad:
                        errors.append({"batch_size":len(pending),"error":msg[:1000]}); break
                    stale.extend(sorted(bad))
                    pending=[x for x in pending if x["variant_id"] not in bad]
        rep["updated"]=updated; rep["stale_variant_ids"]=list(dict.fromkeys(stale)); rep["errors"]=errors[:50]
        # Verify every updated target except stale IDs.
        stale_set=set(stale); verified=0; mism=[]
        for x in targets:
            if x["variant_id"] in stale_set: continue
            try:
                v=cli.req("GET",f"/v1/variants/{x['variant_id']}")
                actual_old=ceilrub(v.get("price") or (v.get("pricing") or {}).get("price"))
                actual_sale=ceilrub(v.get("manual_discount_price") or (v.get("pricing") or {}).get("manual_discount_price"))
                if actual_old==x["old"] and actual_sale==x["sale"]: verified+=1
                elif len(mism)<100: mism.append({"article":x["article"],"actual":[actual_old,actual_sale],"expected":[x["old"],x["sale"]]})
            except Exception as e:
                if len(mism)<100: mism.append({"article":x["article"],"error":str(e)[:600]})
        rep["verified"]=verified; rep["verification_mismatch"]=mism
        rep["minimum_price_note"]="KIT API does not expose a supported separate minimum-price field; KIT Минимальная цена remains internal."
        rep["status"]="УСПЕШНО" if updated==len(targets) and verified==len(targets) and not errors and not stale else "ЧАСТИЧНО"
    except Exception as e:
        rep["status"]="ОШИБКА"; rep["fatal_error"]=str(e)[:2500]
    rep["finished_at"]=now()
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(rep,ensure_ascii=False,indent=2))
    raise SystemExit(0 if rep["status"]=="УСПЕШНО" else 1)

if __name__=="__main__": main()
