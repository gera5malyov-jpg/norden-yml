#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"webasyst"))
from client import WebasystClient

BASEROW_URL=os.environ.get("BASEROW_URL","http://147.78.67.6").rstrip("/")
BASEROW_TOKEN=os.environ.get("BASEROW_DATABASE_TOKEN","").strip()
BASEROW_EMAIL=os.environ.get("BASEROW_EMAIL","").strip()
BASEROW_PASSWORD=os.environ.get("BASEROW_PASSWORD","").strip()
WEBASYST_BASE=os.environ.get("WEBASYST_BASE_URL","https://profikompany.ru").rstrip("/")
WEBASYST_TOKEN=os.environ.get("WEBASYST_API_TOKEN","").strip()
CATALOG_TABLE=156
SUPPLIERS_TABLE=157
SOURCE_TYPE="RED-Black1"
SUPPLIER_NAME="Red-Black"
REPORT=ROOT/"baserow"/"redblack_webasyst_import_report.json"

CORE_FIELDS={
    "Название":("text",None),
    "Артикул":("text",None),
    "Наименование артикула":("text",None),
    "Артикул поставщика":("text",None),
    "Закупка Red-Black":("number",2),
    "Остаток Red-Black":("number",0),
    "Цена продажи Webasyst":("number",2),
    "Цена зачеркнутая Webasyst":("number",2),
    "Первое изображение URL":("url",None),
    "Все изображения":("long_text",None),
    "Webasyst ID товара":("text",None),
    "Webasyst ID SKU":("text",None),
    "Webasyst тип":("text",None),
    "Webasyst URL":("url",None),
    "Webasyst статус":("text",None),
    "Webasyst описание":("long_text",None),
    "Webasyst краткое описание":("long_text",None),
    "Webasyst категории JSON":("long_text",None),
    "Webasyst SKU склады JSON":("long_text",None),
    "Webasyst изображения JSON":("long_text",None),
    "Webasyst RAW JSON":("long_text",None),
}

def s(v): return str(v or "").strip()
def norm(v): return unicodedata.normalize("NFKC",s(v)).casefold()
def sku_key(v): return re.sub(r"\s+","",norm(v))
def now(): return datetime.now(timezone.utc).isoformat()

def listify(payload,keys=()):
    if isinstance(payload,list): return [x for x in payload if isinstance(x,dict)]
    if not isinstance(payload,dict): return []
    for k in keys:
        v=payload.get(k)
        if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
        if isinstance(v,dict): return [x for x in v.values() if isinstance(x,dict)]
    if payload and all(isinstance(x,dict) for x in payload.values()):
        return list(payload.values())
    return []

def scalar_text(v):
    if v is None: return ""
    if isinstance(v,bool): return "true" if v else "false"
    if isinstance(v,(str,int,float)): return str(v)
    return json.dumps(v,ensure_ascii=False,separators=(",",":"),default=str)

def flatten_feature_value(v):
    if v is None: return ""
    if isinstance(v,(str,int,float,bool)): return scalar_text(v)
    if isinstance(v,list):
        parts=[]
        for x in v:
            if isinstance(x,dict):
                val=x.get("value") if "value" in x else x.get("name") if "name" in x else x
                txt=flatten_feature_value(val)
            else:
                txt=flatten_feature_value(x)
            if txt and txt not in parts: parts.append(txt)
        return " | ".join(parts)
    if isinstance(v,dict):
        for key in ("value","name","title"):
            if key in v and v[key] not in (None,""):
                return flatten_feature_value(v[key])
        return json.dumps(v,ensure_ascii=False,separators=(",",":"),default=str)
    return str(v)

def safe_field_name(prefix,key):
    base=f"{prefix}{key}".strip()
    base=re.sub(r"[\r\n\t]+"," ",base)
    if len(base)<=240:return base
    h=hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]
    return base[:225]+" — "+h

def extract_urls(info):
    chunks=[]
    for k in ("summary","description"):
        if info.get(k): chunks.append(str(info.get(k)))
    if info.get("images") is not None:
        chunks.append(json.dumps(info.get("images"),ensure_ascii=False,default=str))
    text="\n".join(chunks)
    urls=re.findall(r"https?://[^\s\[\]<>\"']+",text)
    cleaned=[]
    for u in urls:
        u=u.rstrip(".,);")
        if u and u not in cleaned: cleaned.append(u)
    return cleaned

def as_float(v):
    try:return float(str(v).replace("\xa0","").replace(" ","").replace(",","."))
    except:return None

def sku_stock(sku):
    vals=[]
    stocks=sku.get("stocks")
    if isinstance(stocks,dict):
        iterable=[]
        for k,v in stocks.items():
            iterable.append({"id":k,"count":v})
    elif isinstance(stocks,list):
        iterable=stocks
    else:
        iterable=[]
    for row in iterable:
        if not isinstance(row,dict): continue
        v=as_float(row.get("count") if "count" in row else row.get("quantity"))
        if v is not None: vals.append(max(0,v))
    if vals:return int(round(sum(vals)))
    v=as_float(sku.get("count"))
    return max(0,int(round(v))) if v is not None else 0

class BR:
    def __init__(self):
        self.ses=requests.Session()
        self.ses.headers.update({"Accept":"application/json","Content-Type":"application/json"})
        if BASEROW_TOKEN:
            self.ses.headers["Authorization"]="Token "+BASEROW_TOKEN
        elif BASEROW_EMAIL and BASEROW_PASSWORD:
            auth=self.ses.post(
                BASEROW_URL+"/api/user/token-auth/",
                json={"username":BASEROW_EMAIL,"password":BASEROW_PASSWORD},
                timeout=60,
            )
            if not auth.ok:
                auth=self.ses.post(
                    BASEROW_URL+"/api/user/token-auth/",
                    json={"email":BASEROW_EMAIL,"password":BASEROW_PASSWORD},
                    timeout=60,
                )
            if not auth.ok:
                raise RuntimeError(f"Baserow login failed: HTTP {auth.status_code}: {auth.text[:1000]}")
            self.ses.headers["Authorization"]="JWT "+auth.json()["token"]
        else:
            raise RuntimeError("BASEROW credentials missing")
        self._fields={}
        self.refresh_fields()
    def req(self,m,p,body=None):
        last=None
        for attempt in range(8):
            try:
                r=self.ses.request(m,BASEROW_URL+p,json=body,timeout=90)
            except requests.RequestException as e:
                last=e; time.sleep(min(15,2**attempt)); continue
            if r.status_code==429 or r.status_code>=500:
                last=RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
                time.sleep(float(r.headers.get("Retry-After") or min(20,2**attempt))); continue
            if not r.ok: raise RuntimeError(f"Baserow {m} {p}: HTTP {r.status_code}: {r.text[:1200]}")
            return r.json() if r.content else None
        raise RuntimeError(str(last))
    def refresh_fields(self):
        rows=self.req("GET",f"/api/database/fields/table/{CATALOG_TABLE}/") or []
        self._fields={s(x.get("name")):x for x in rows}
    def ensure(self,name,kind="text",decimals=None):
        if name in self._fields:
            return self._fields[name]
        # The initial RED-Black transfer intentionally does not mutate schema
        # with the database token. Core/dynamic fields were prepared separately
        # with admin credentials. Missing optional fields stay preserved in RAW JSON.
        if os.environ.get("RED_BLACK_ALLOW_SCHEMA_CREATE","0").strip() != "1":
            return None
        body={"name":name,"type":kind}
        if kind=="number":
            body["number_decimal_places"]=int(decimals or 0); body["number_negative"]=False
        f=self.req("POST",f"/api/database/fields/table/{CATALOG_TABLE}/",body)
        self._fields[name]=f
        return f
    def rows(self,tid):
        out=[]; page=1
        while True:
            d=self.req("GET",f"/api/database/rows/table/{tid}/?user_field_names=true&size=200&page={page}")
            out.extend(d.get("results") or [])
            if not d.get("next"): return out
            page+=1
    def create_supplier(self):
        rows=self.rows(SUPPLIERS_TABLE)
        m=[r for r in rows if norm(r.get("Поставщик"))==norm(SUPPLIER_NAME)]
        if len(m)>1: raise RuntimeError(f"Duplicate supplier rows {SUPPLIER_NAME}: {[x.get('id') for x in m]}")
        if len(m)==1:return int(m[0]["id"]),False
        row=self.req("POST",f"/api/database/rows/table/{SUPPLIERS_TABLE}/?user_field_names=true",{"Поставщик":SUPPLIER_NAME})
        return int(row["id"]),True
    def batch_create(self,items):
        for i in range(0,len(items),100):
            self.req("POST",f"/api/database/rows/table/{CATALOG_TABLE}/batch/?user_field_names=true",{"items":items[i:i+100]})
    def batch_update(self,items):
        for i in range(0,len(items),100):
            self.req("PATCH",f"/api/database/rows/table/{CATALOG_TABLE}/batch/?user_field_names=true",{"items":items[i:i+100]})

def field_value_for_existing_type(br,name,value):
    f=br._fields.get(name) or {}
    typ=s(f.get("type"))
    if value is None:return None
    if typ=="number":
        v=as_float(value)
        return None if v is None else v
    if typ=="boolean":
        if isinstance(value,bool):return value
        return norm(value) in ("1","true","yes","да")
    if typ in ("text","long_text","url","email","phone_number"):
        return scalar_text(value)
    # Do not risk corrupting special/select/link/file fields with arbitrary text.
    return None

def main():
    if not WEBASYST_TOKEN: raise RuntimeError("WEBASYST_API_TOKEN missing")
    br=BR()
    for name,(kind,dec) in CORE_FIELDS.items():
        br.ensure(name,kind,dec)

    supplier_id,supplier_created=br.create_supplier()
    wa=WebasystClient(base_url=WEBASYST_BASE,token=WEBASYST_TOKEN,min_request_interval=0.34)

    types=listify(wa.call("shop.type.getList"))
    tm=[x for x in types if norm(x.get("name") or x.get("title"))==norm(SOURCE_TYPE)]
    if len(tm)!=1: raise RuntimeError(f"Expected one Webasyst type {SOURCE_TYPE}, got {len(tm)}")
    type_id=s(tm[0]["id"])

    stocks=listify(wa.call("shop.stock.getList"))
    stock_name={s(x.get("id")):s(x.get("name") or x.get("title") or x.get("id")) for x in stocks if s(x.get("id"))}
    # Stock columns are prepared separately with admin permissions. Only Red МСК
    # is expected for this supplier; all raw stock data is also preserved in JSON.
    feature_defs=listify(wa.call("shop.feature.getList"),("features","items"))
    feature_by_code={s(x.get("code")):s(x.get("name") or x.get("title") or x.get("code")) for x in feature_defs if s(x.get("code"))}

    products=[]; offset=0
    while True:
        d=wa.call("shop.product.search",params={
            "hash":f"type/{type_id}","offset":offset,"limit":1000,
            "fields":"id,name,type_id,summary,description,skus,image_id,image_filename,status,url"
        })
        batch=listify(d,("products","items")); products.extend(batch)
        if not batch or len(batch)<1000:break
        offset+=len(batch)

    source_skus=[]
    for p in products:
        for sk in listify(p.get("skus") or [],()):
            sku=s(sk.get("sku"))
            if sku: source_skus.append(sku_key(sku))
    counts=Counter(source_skus)
    dup_source={k for k,v in counts.items() if v>1}

    catalog=br.rows(CATALOG_TABLE)
    by_article=defaultdict(list)
    for row in catalog:
        a=sku_key(row.get("Артикул"))
        if a: by_article[a].append(row)
    dup_db={k for k,v in by_article.items() if len(v)>1}

    created=[]; updated=[]
    errors=[]; processed=0; images_rows=0; feature_values=0
    fields_created_before=len(br._fields)
    missing_optional_fields=set()

    def flush():
        nonlocal created,updated
        if created: br.batch_create(created); created=[]
        if updated: br.batch_update(updated); updated=[]

    for pos,p in enumerate(products,1):
        pid=s(p.get("id"))
        try:
            info=wa.call("shop.product.getInfo",params={"id":pid})
            if not isinstance(info,dict): raise RuntimeError("getInfo returned non-object")
            features=info.get("features") if isinstance(info.get("features"),dict) else {}
            urls=extract_urls(info)
            if urls: images_rows+=1
            skus=listify(info.get("skus") or [],())
            for sk in skus:
                sku=s(sk.get("sku"))
                if not sku: continue
                key=sku_key(sku)
                if key in dup_source:
                    errors.append({"sku":sku,"product_id":pid,"stage":"duplicate_source_sku"}); continue
                if key in dup_db:
                    errors.append({"sku":sku,"product_id":pid,"stage":"duplicate_baserow_article","row_ids":[x.get("id") for x in by_article[key]]}); continue

                body={
                    "Название":s(info.get("name")) or sku,
                    "Артикул":sku,
                    "Поставщик":[supplier_id],
                    "Закупка Red-Black":as_float(sk.get("purchase_price")) or 0,
                    "Остаток Red-Black":sku_stock(sk),
                    "Цена продажи Webasyst":as_float(sk.get("price")) or 0,
                    "Цена зачеркнутая Webasyst":as_float(sk.get("compare_price")) or 0,
                    "Наличие":bool(sku_stock(sk)>0 and norm(sk.get("available")) not in ("0","false","нет")),
                    "Первое изображение URL":urls[0] if urls else "",
                    "Все изображения":"\n".join(urls),
                    "Webasyst ID товара":pid,
                    "Webasyst ID SKU":s(sk.get("id")),
                    "Webasyst тип":SOURCE_TYPE,
                    "Webasyst URL":(WEBASYST_BASE.rstrip("/")+"/"+s(info.get("url")).lstrip("/")) if s(info.get("url")) else "",
                    "Webasyst статус":s(info.get("status")),
                    "Webasyst описание":s(info.get("description")),
                    "Webasyst краткое описание":s(info.get("summary")),
                    "Webasyst категории JSON":json.dumps(info.get("categories") or [],ensure_ascii=False,separators=(",",":"),default=str),
                    "Webasyst SKU склады JSON":json.dumps(sk.get("stocks") or [],ensure_ascii=False,separators=(",",":"),default=str),
                    "Webasyst изображения JSON":json.dumps(info.get("images") or [],ensure_ascii=False,separators=(",",":"),default=str),
                }

                # Every scalar product field gets its own technical column.
                for k,v in info.items():
                    if k in ("features","skus","images","categories","description","summary","name","url","status"): continue
                    if isinstance(v,(dict,list,tuple)): continue
                    fname=safe_field_name("Webasyst товар — ",k)
                    if br.ensure(fname,"text",None) is not None:
                        body[fname]=scalar_text(v)
                    else:
                        missing_optional_fields.add(fname)

                # Every scalar SKU field gets its own technical column.
                for k,v in sk.items():
                    if k in ("stocks","sku","id","price","purchase_price","compare_price"): continue
                    if isinstance(v,(dict,list,tuple)): continue
                    fname=safe_field_name("Webasyst SKU — ",k)
                    br.ensure(fname,"text",None)
                    body[fname]=scalar_text(v)

                # Warehouse stock columns.
                raw_stocks=sk.get("stocks") or []
                if isinstance(raw_stocks,dict):
                    stock_iter=[{"id":k,"count":v} for k,v in raw_stocks.items()]
                elif isinstance(raw_stocks,list):
                    stock_iter=raw_stocks
                else:
                    stock_iter=[]
                for st in stock_iter:
                    if not isinstance(st,dict):continue
                    sid=s(st.get("id") or st.get("stock_id"))
                    title=stock_name.get(sid,sid or "без ID")
                    fname=safe_field_name("Webasyst остаток — ",title)
                    val=as_float(st.get("count") if "count" in st else st.get("quantity"))
                    if fname in br._fields:
                        body[fname]=max(0,val or 0)

                # Webasyst characteristics are written exactly as named in Webasyst:
                # "Название цвета", "Серия", "Высота", etc. No supplier prefix.
                for code,val in features.items():
                    title=s(feature_by_code.get(s(code),s(code)))
                    if not title:
                        continue
                    fname=safe_field_name("",title)
                    txt=flatten_feature_value(val)
                    if br.ensure(fname,"long_text",None) is not None:
                        body[fname]=txt
                    else:
                        missing_optional_fields.add(fname)
                    if txt: feature_values+=1
                    if norm(code)=="artikul" or norm(title)=="артикул":
                        if txt:
                            body["Наименование артикула"]=txt
                            body["Артикул поставщика"]=txt

                body["Webasyst RAW JSON"]=json.dumps(
                    {"product":{k:v for k,v in info.items() if k!="skus"},"sku":sk},
                    ensure_ascii=False,separators=(",",":"),default=str
                )

                # Remove values that cannot be written to an existing special-type field.
                safe={}
                for name,val in body.items():
                    if name=="Поставщик":
                        safe[name]=val; continue
                    if name not in br._fields:
                        continue
                    coerced=field_value_for_existing_type(br,name,val)
                    if coerced is not None:
                        safe[name]=coerced
                if key in by_article and len(by_article[key])==1:
                    safe["id"]=by_article[key][0]["id"]; updated.append(safe)
                else:
                    created.append(safe)
                    # prevent duplicate creates during this same run
                    by_article[key]=[{"id":None}]
                processed+=1

                if len(created)+len(updated)>=100:
                    flush()
            if pos%100==0:
                print(f"PROGRESS products={pos}/{len(products)} rows={processed}",flush=True)
        except Exception as e:
            errors.append({"product_id":pid,"stage":"product","error":str(e)[:1200]})
        if len(errors)>500:
            raise RuntimeError("Too many errors; stopping import")

    flush()
    fields_created=len(br._fields)-fields_created_before
    final_rows=br.rows(CATALOG_TABLE)
    red_rows=[r for r in final_rows if any(isinstance(x,dict) and int(x.get("id") or 0)==supplier_id for x in (r.get("Поставщик") or []))]
    report={
        "started_at":now(),
        "source":"Webasyst",
        "type":SOURCE_TYPE,
        "type_id":type_id,
        "supplier":SUPPLIER_NAME,
        "supplier_id":supplier_id,
        "supplier_created":supplier_created,
        "webasyst_products":len(products),
        "source_skus":len(source_skus),
        "duplicate_source_skus":len(dup_source),
        "duplicate_baserow_articles":len(dup_db),
        "processed_rows":processed,
        "redblack_rows_after":len(red_rows),
        "rows_with_image_links":images_rows,
        "feature_values_written":feature_values,
        "fields_created":fields_created,
        "missing_optional_fields_count":len(missing_optional_fields),
        "missing_optional_fields_sample":sorted(missing_optional_fields)[:100],
        "errors_count":len(errors),
        "errors_sample":errors[:100],
        "image_policy":"Only URL links from Webasyst summary/description/images; no image files uploaded to Baserow.",
        "match_policy":"Exact SKU -> Baserow Артикул; update one exact match, create if absent, skip duplicates.",
        "finished_at":now(),
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    if dup_source or dup_db or errors:
        return 2
    return 0

if __name__=="__main__":
    raise SystemExit(main())
