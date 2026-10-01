#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import re
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

import requests

BASEROW_URL=os.environ.get("BASEROW_URL","http://147.78.67.6").rstrip("/")
BASEROW_TOKEN=os.environ.get("BASEROW_DATABASE_TOKEN","").strip()
KIT_TOKEN=os.environ.get("YANDEX_KIT_TOKEN","").strip()
KIT_BASE="https://api.kit.yandex.net"
CATALOG_TABLE_ID=156
SUPPLIERS_TABLE_ID=157
FIELD_KIT="Артикул KIT"
SUPPLIER_NAME="Norden"
REPORT=Path("baserow/kit_id_sync_report.json")
LIVE_INDEX=Path("baserow/kit_live_index.json")
IDENTITY_TITLES={
    "артикул","код продавца","код norden","внешний id",
    "внешний идентификатор","external id","код для сайта"
}
CONFUSABLES=str.maketrans({
    "а":"a","в":"b","с":"c","е":"e","н":"h","к":"k","м":"m","о":"o","р":"p","т":"t","х":"x","у":"y",
    "А":"a","В":"b","С":"c","Е":"e","Н":"h","К":"k","М":"m","О":"o","Р":"p","Т":"t","Х":"x","У":"y",
})

def s(v): return str(v or "").strip()
def norm(v):
    x=unicodedata.normalize("NFKC",s(v)).translate(CONFUSABLES).casefold()
    return re.sub(r"[^0-9a-z]+","",x)

def req(session,method,url,**kwargs):
    last=None
    for attempt in range(10):
        try:
            r=session.request(method,url,timeout=90,**kwargs)
        except requests.RequestException as exc:
            last=exc; time.sleep(min(20,2**attempt)); continue
        last=r
        if r.status_code==429 or r.status_code>=500:
            time.sleep(float(r.headers.get("Retry-After") or min(30,2**attempt))); continue
        if not r.ok:
            raise RuntimeError(f"{method} {url} HTTP {r.status_code}: {r.text[:1000]}")
        return r.json() if r.content else {}
    raise RuntimeError(f"request failed: {last}")

class Baserow:
    def __init__(self):
        self.s=requests.Session()
        self.s.headers.update({"Authorization":f"Token {BASEROW_TOKEN}","Accept":"application/json","Content-Type":"application/json"})
    def get(self,path): return req(self.s,"GET",BASEROW_URL+path)
    def rows(self,tid):
        out=[]; page=1
        while True:
            d=self.get(f"/api/database/rows/table/{tid}/?user_field_names=true&size=200&page={page}")
            out.extend(d.get("results") or [])
            if not d.get("next"): return out
            page+=1
    def batch_update(self,items):
        for i in range(0,len(items),100):
            batch=items[i:i+100]
            req(self.s,"PATCH",BASEROW_URL+f"/api/database/rows/table/{CATALOG_TABLE_ID}/batch/?user_field_names=true",json={"items":batch})

def supplier_ids(row):
    out=set()
    for x in row.get("Поставщик") or []:
        if isinstance(x,dict) and x.get("id") is not None:
            try: out.add(int(x["id"]))
            except Exception: pass
    return out

def char_value(row,cid):
    for c in row.get("characteristics") or []:
        if s(c.get("characteristic_id"))==s(cid):
            if s(c.get("value")): return s(c.get("value"))
            vals=c.get("values") or []
            if vals: return s(vals[0])
    return ""

def kit_items(payload):
    if isinstance(payload,list): return payload
    if not isinstance(payload,dict): return []
    for k in ("items","results","variants","characteristics","data"):
        v=payload.get(k)
        if isinstance(v,list): return v
        if isinstance(v,dict) and isinstance(v.get("items"),list): return v["items"]
    return []

def main():
    if not BASEROW_TOKEN or not KIT_TOKEN:
        raise RuntimeError("missing BASEROW_DATABASE_TOKEN or YANDEX_KIT_TOKEN")
    br=Baserow()
    suppliers=br.rows(SUPPLIERS_TABLE_ID)
    n=[x for x in suppliers if s(x.get("Поставщик")).casefold()==SUPPLIER_NAME.casefold()]
    if len(n)!=1: raise RuntimeError(f"expected one Norden supplier, got {len(n)}")
    sid=int(n[0]["id"])
    rows=[r for r in br.rows(CATALOG_TABLE_ID) if sid in supplier_ids(r) or s(r.get("Бренд")).casefold()=="norden"]

    key_to_rows=defaultdict(set)
    row_by_id={int(r["id"]):r for r in rows}
    for r in rows:
        vals=[
            r.get("Артикул"),
            r.get("Наименование артикула"),
            r.get("Артикул поставщика"),
            r.get("Код для сайта"),
            r.get("Код Norden"),
        ]
        for v in vals:
            k=norm(v)
            if k: key_to_rows[k].add(int(r["id"]))

    ks=requests.Session()
    ks.headers.update({"Authorization":"Bearer "+KIT_TOKEN,"Accept":"application/json"})
    chars=[]
    page=1
    while True:
        d=req(ks,"GET",KIT_BASE+"/v1/characteristics",params={"status":"ACTIVE","page":page,"per_page":100})
        batch=kit_items(d); chars.extend(x for x in batch if isinstance(x,dict))
        if not batch or len(batch)<100: break
        page+=1
    id_chars={s(x.get("id")):s(x.get("title")) for x in chars if s(x.get("title")).casefold() in IDENTITY_TITLES}

    candidates=defaultdict(list)
    live_index=defaultdict(list)
    scanned=norden_seen=0
    page=1
    while True:
        d=req(ks,"GET",KIT_BASE+"/v1/variants",params={"page":page,"per_page":100})
        batch=kit_items(d)
        if not batch: break
        for v in batch:
            scanned+=1
            if s(v.get("brand")).casefold()!="norden": continue
            if s(v.get("status")).upper()=="ARCHIVED": continue
            norden_seen+=1
            values=[("SKU",s(v.get("sku")))]
            for cid,title in id_chars.items():
                val=char_value(v,cid)
                if val: values.append((title,val))
            row_hits=set()
            for _,val in values:
                k=norm(val)
                if k and len(key_to_rows.get(k,()))==1:
                    row_hits.update(key_to_rows[k])
            kid=v.get("kit_id")
            try: kid=int(str(kid).strip())
            except Exception: kid=0
            vid=s(v.get("id"))
            if kid>0 and vid:
                live_index[kid].append({"variant_id":vid,"sku":s(v.get("sku"))})
            if len(row_hits)==1 and kid>0:
                rid=next(iter(row_hits))
                candidates[rid].append({"kit_id":kid,"variant_id":vid,"sku":s(v.get("sku")),"values":values})
        if len(batch)<100: break
        page+=1

    updates=[]; ambiguous=[]; unchanged=0; matched=0
    for rid,r in row_by_id.items():
        cs=candidates.get(rid,[])
        by_kid={x["kit_id"]:x for x in cs}
        if not by_kid: continue
        chosen=None
        if len(by_kid)==1:
            chosen=next(iter(by_kid.values()))
        else:
            internal=s(r.get("Артикул"))
            exact=[x for x in by_kid.values() if s(x.get("sku"))==internal and internal]
            if len(exact)==1: chosen=exact[0]
        if not chosen:
            ambiguous.append({"row_id":rid,"article":r.get("Артикул"),"supplier_article":r.get("Наименование артикула"),"kit_ids":sorted(by_kid)})
            continue
        matched+=1
        cur=r.get(FIELD_KIT)
        try: same=int(float(cur))==int(chosen["kit_id"])
        except Exception: same=False
        if same:
            unchanged+=1
        else:
            updates.append({"id":rid,FIELD_KIT:chosen["kit_id"]})
    if updates: br.batch_update(updates)

    REPORT.parent.mkdir(parents=True,exist_ok=True)
    LIVE_INDEX.write_text(json.dumps({str(k):v for k,v in live_index.items()},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    out={
        "baserow_norden_rows":len(rows),"kit_variants_scanned":scanned,"kit_norden_seen":norden_seen,
        "identity_characteristics":id_chars,"matched_rows":matched,"updated_rows":len(updates),
        "unchanged_rows":unchanged,"ambiguous_rows":len(ambiguous),"ambiguous_sample":ambiguous[:100],
        "rows_without_live_match":len(rows)-matched-len(ambiguous),
    }
    REPORT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
