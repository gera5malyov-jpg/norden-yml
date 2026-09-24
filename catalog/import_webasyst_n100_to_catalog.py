#!/usr/bin/env python3
from __future__ import annotations
import json, os, re, time, unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
import gspread
from google.oauth2.service_account import Credentials

TYPE_NAME = "NORDEN-100"
SPREADSHEET_ID = os.environ["CATALOG_SPREADSHEET_ID"].strip()
SHEET_NAME = os.environ.get("CATALOG_SHEET", "Норден").strip()
WA_BASE = (os.environ.get("WEBASYST_BASE_URL") or "https://profikompany.ru").rstrip("/")
WA_TOKEN = os.environ["WEBASYST_API_TOKEN"].strip()
SA_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
REPORT = Path("catalog/webasyst_n100_catalog_report.json")

OZON_BLUE = {"red": 0.39, "green": 0.58, "blue": 0.93}
WA_LIGHT_BLUE = {"red": 0.80, "green": 0.90, "blue": 0.98}

EXTRA_HEADERS = [
    "Источник",
    "Webasyst product_id",
    "Webasyst sku_id",
    "Тип Webasyst при загрузке",
    "Webasyst URL",
    "Описание Webasyst",
    "Краткое описание Webasyst",
    "Цена Webasyst",
    "Старая цена Webasyst",
    "Закупочная цена Webasyst",
    "Остатки Webasyst JSON",
    "Изображения Webasyst JSON",
    "Категории Webasyst JSON",
    "Характеристики Webasyst JSON",
    "Webasyst product JSON",
    "Webasyst SKU JSON",
    "Webasyst info JSON",
]

def s(v): return str(v or "").strip()
def norm(v): return unicodedata.normalize("NFKC", s(v)).casefold()
def nt(v): return re.sub(r"[^0-9a-zа-яё]+", "", norm(v))
def j(v): return json.dumps(v, ensure_ascii=False, separators=(",",":"))

def listify(payload, keys=()):
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict): return []
    for key in keys:
        v=payload.get(key)
        if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
        if isinstance(v,dict): return [x for x in v.values() if isinstance(x,dict)]
    if payload and all(isinstance(v,dict) for v in payload.values()):
        return list(payload.values())
    return []

def skus(product):
    v=product.get("skus")
    if isinstance(v,dict): return [x for x in v.values() if isinstance(x,dict)]
    if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
    return []

class WA:
    def __init__(self):
        self.session=requests.Session()
        self.session.headers.update({"Accept":"application/json","User-Agent":"megapolis-catalog-n100/1.0"})
        self.last=0.0
    def call(self, method, params=None):
        p=dict(params or {})
        p["format"]="json"; p["access_token"]=WA_TOKEN
        for attempt in range(10):
            delay=0.18-(time.monotonic()-self.last)
            if delay>0: time.sleep(delay)
            self.last=time.monotonic()
            r=self.session.get(f"{WA_BASE}/api.php/{method}",params=p,timeout=90)
            if r.status_code==429:
                time.sleep(float(r.headers.get("Retry-After") or min(30,2*(attempt+1)))); continue
            if r.status_code>=500:
                time.sleep(min(20,2**attempt)); continue
            try: data=r.json()
            except Exception: raise RuntimeError(f"{method}: non-JSON HTTP {r.status_code}")
            if r.status_code>=400: raise RuntimeError(f"{method}: HTTP {r.status_code}: {str(data)[:700]}")
            if isinstance(data,dict) and data.get("error"):
                raise RuntimeError(f"{method}: {data.get('error')}: {data.get('error_description') or ''}")
            return data
        raise RuntimeError(f"{method}: retries exhausted")

def load_products(wa, type_id):
    out=[]; offset=0
    while True:
        d=wa.call("shop.product.search",{
            "hash":f"type/{type_id}","offset":offset,"limit":1000,
            "fields":"*,skus,stock_counts"
        })
        batch=listify(d,("products","items"))
        out.extend(batch)
        total=(d.get("count") or d.get("total_count")) if isinstance(d,dict) else None
        if not batch or len(batch)<1000: break
        if total not in (None,"") and len(out)>=int(total): break
        offset+=len(batch)
    return out

def category_map(payload):
    by_id={}
    def walk(node,path):
        if not isinstance(node,dict): return
        cid=s(node.get("id")); title=s(node.get("name") or node.get("title"))
        here=path+([title] if title else [])
        if cid: by_id[cid]=here
        children=node.get("children") or node.get("childs") or node.get("categories") or []
        if isinstance(children,dict): children=list(children.values())
        if isinstance(children,list):
            for ch in children: walk(ch,here)
    for row in listify(payload,("categories","items")): walk(row,[])
    return by_id

def category_ids(info,basic):
    vals=[]
    for obj in (info,basic):
        if not isinstance(obj,dict): continue
        for k in ("category_id","category"):
            v=obj.get(k)
            if isinstance(v,(str,int)) and s(v): vals.append(s(v))
        for k in ("category_ids","categories"):
            v=obj.get(k)
            if isinstance(v,dict):
                for key,item in v.items():
                    if s(key): vals.append(s(key))
                    if isinstance(item,dict) and s(item.get("id")): vals.append(s(item.get("id")))
                    elif isinstance(item,(str,int)) and s(item): vals.append(s(item))
            elif isinstance(v,list):
                for item in v:
                    if isinstance(item,dict) and s(item.get("id")): vals.append(s(item.get("id")))
                    elif isinstance(item,(str,int)) and s(item): vals.append(s(item))
    return list(dict.fromkeys(vals))

def image_urls(info):
    rows=(info or {}).get("images") or []
    if isinstance(rows,dict): rows=list(rows.values())
    out=[]
    for row in rows if isinstance(rows,list) else []:
        if not isinstance(row,dict): continue
        u=s(row.get("url_big") or row.get("url") or row.get("url_thumb"))
        if u: out.append(urljoin(WA_BASE+"/",u))
    return list(dict.fromkeys(out))

def features(info, feature_titles):
    raw=(info or {}).get("features") or {}
    out={}
    if isinstance(raw,dict):
        for code,val in raw.items():
            title=feature_titles.get(s(code)) or s(code)
            out[title]=val
    elif isinstance(raw,list):
        for item in raw:
            if not isinstance(item,dict): continue
            code=s(item.get("code") or item.get("id"))
            title=s(item.get("name") or item.get("title")) or feature_titles.get(code) or code
            out[title]=item.get("value") if "value" in item else item
    return out

def image_formula(url):
    if not url: return ""
    return '=IMAGE("' + str(url).replace('"','""') + '")'

def main():
    wa=WA()
    types=listify(wa.call("shop.type.getList"))
    matches=[x for x in types if nt(x.get("name") or x.get("title"))==nt(TYPE_NAME)]
    if len(matches)!=1:
        raise RuntimeError(f"Expected exactly one Webasyst type {TYPE_NAME}, found {len(matches)}")
    type_id=s(matches[0].get("id"))
    products=load_products(wa,type_id)

    try: cmap=category_map(wa.call("shop.category.getTree"))
    except Exception: cmap={}
    try:
        fdefs=listify(wa.call("shop.feature.getList"),("features","items"))
        ftitles={s(x.get("code")):s(x.get("name") or x.get("title")) for x in fdefs if s(x.get("code"))}
    except Exception:
        ftitles={}

    creds=json.loads(SA_JSON)
    gc=gspread.authorize(Credentials.from_service_account_info(
        creds,scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    ))
    sh=gc.open_by_key(SPREADSHEET_ID)
    ws=sh.worksheet(SHEET_NAME)

    all_values=ws.get_all_values()
    if not all_values: raise RuntimeError("Норден sheet is empty")
    headers=list(all_values[0])
    for h in EXTRA_HEADERS:
        if h not in headers: headers.append(h)

    if ws.col_count < len(headers):
        ws.resize(cols=len(headers))
    ws.update(range_name=f"A1:{gspread.utils.rowcol_to_a1(1,len(headers))}", values=[headers], value_input_option="RAW")

    hidx={h:i for i,h in enumerate(headers)}
    article_col=hidx["Артикул"]
    photo_col=hidx["Основное фото"]

    existing_rows={}
    ozon_articles=set()
    for rowno,row in enumerate(all_values[1:],start=2):
        article=s(row[article_col] if article_col<len(row) else "")
        if not article: continue
        existing_rows[norm(article)]=rowno
        pid=s(row[hidx["Ozon product_id"]] if hidx["Ozon product_id"]<len(row) else "")
        if pid: ozon_articles.add(norm(article))

    # Preserve every existing value. Only requested metadata/photo presentation are touched.
    source_col=hidx["Источник"]
    type_col=hidx["Тип Webasyst при загрузке"]
    meta_updates=[]
    for key,rowno in existing_rows.items():
        meta_updates.append({"range":f"{gspread.utils.rowcol_to_a1(rowno,source_col+1)}","values":[["Ozon" if key in ozon_articles else "Webasyst"]]})
        meta_updates.append({"range":f"{gspread.utils.rowcol_to_a1(rowno,type_col+1)}","values":[[TYPE_NAME]]})

    # Convert existing Ozon photo URLs to visible images.
    for rowno,row in enumerate(all_values[1:],start=2):
        if photo_col>=len(row): continue
        val=s(row[photo_col])
        if val.startswith("http://") or val.startswith("https://"):
            meta_updates.append({"range":f"{gspread.utils.rowcol_to_a1(rowno,photo_col+1)}","values":[[image_formula(val)]]})

    if meta_updates:
        # Sheets API batchUpdate values; split to avoid request size.
        for i in range(0,len(meta_updates),300):
            ws.batch_update(meta_updates[i:i+300], value_input_option="USER_ENTERED")

    added=0; skipped_existing=0; duplicate_wa=0; errors=[]
    seen_wa=set()
    new_rows=[]
    for p in products:
        pid=s(p.get("id"))
        for sku in skus(p):
            article=s(sku.get("sku"))
            if not article: continue
            key=norm(article)
            if key in seen_wa:
                duplicate_wa+=1; continue
            seen_wa.add(key)
            if key in existing_rows:
                skipped_existing+=1
                continue
            try:
                info=wa.call("shop.product.getInfo",{"id":pid})
                imgs=image_urls(info)
                cids=category_ids(info,p)
                cats=[cmap[cid] for cid in cids if cid in cmap]
                feat=features(info,ftitles)
                name=s((info or {}).get("name")) or s(p.get("name")) or article
                url=s((info or {}).get("frontend_url") or (info or {}).get("url"))
                if url and not url.startswith("http"): url=urljoin(WA_BASE+"/",url)
                stock=sku.get("stock_counts") if sku.get("stock_counts") not in (None,"") else p.get("stock_counts")
                row=[""]*len(headers)
                def put(h,v):
                    if h in hidx: row[hidx[h]]=v
                put("Артикул",article)
                put("Название",name)
                put("Бренд","Norden")
                put("Основное фото",image_formula(imgs[0]) if imgs else "")
                put("Фото",j(imgs))
                put("Источник","Webasyst")
                put("Webasyst product_id",pid)
                put("Webasyst sku_id",s(sku.get("id")))
                put("Тип Webasyst при загрузке",TYPE_NAME)
                put("Webasyst URL",url)
                put("Описание Webasyst",s((info or {}).get("description") or p.get("description")))
                put("Краткое описание Webasyst",s((info or {}).get("summary") or p.get("summary")))
                put("Цена Webasyst",s(sku.get("price") or p.get("price")))
                put("Старая цена Webasyst",s(sku.get("compare_price") or p.get("compare_price")))
                put("Закупочная цена Webasyst",s(sku.get("purchase_price") or p.get("purchase_price")))
                put("Остатки Webasyst JSON",j(stock or {}))
                put("Изображения Webasyst JSON",j(imgs))
                put("Категории Webasyst JSON",j(cats))
                put("Характеристики Webasyst JSON",j(feat))
                put("Webasyst product JSON",j(p))
                put("Webasyst SKU JSON",j(sku))
                put("Webasyst info JSON",j(info or {}))
                new_rows.append(row)
                added+=1
                existing_rows[key]=len(all_values)+len(new_rows)
            except Exception as exc:
                errors.append({"article":article,"product_id":pid,"error":str(exc)[:1200]})

    start_row=len(all_values)+1
    if new_rows:
        needed=start_row+len(new_rows)+10
        if ws.row_count<needed: ws.resize(rows=needed)
        for i in range(0,len(new_rows),25):
            batch=new_rows[i:i+25]
            r1=start_row+i; r2=r1+len(batch)-1
            ws.update(
                range_name=f"A{r1}:{gspread.utils.rowcol_to_a1(r2,len(headers))}",
                values=batch,value_input_option="USER_ENTERED"
            )

    # Format rows by provenance: Ozon blue, Webasyst-only light blue.
    final_last=start_row+len(new_rows)-1 if new_rows else len(all_values)
    requests=[]
    last_col=len(headers)
    if final_last>=2:
        # Existing Ozon rows
        for key,rowno in existing_rows.items():
            color=OZON_BLUE if key in ozon_articles else WA_LIGHT_BLUE
            requests.append({"repeatCell":{
                "range":{"sheetId":ws.id,"startRowIndex":rowno-1,"endRowIndex":rowno,
                         "startColumnIndex":0,"endColumnIndex":last_col},
                "cell":{"userEnteredFormat":{"backgroundColor":color}},
                "fields":"userEnteredFormat.backgroundColor"
            }})
        # image row height
        requests.append({"updateDimensionProperties":{
            "range":{"sheetId":ws.id,"dimension":"ROWS","startIndex":1,"endIndex":final_last},
            "properties":{"pixelSize":110},"fields":"pixelSize"
        }})
        requests.append({"updateDimensionProperties":{
            "range":{"sheetId":ws.id,"dimension":"COLUMNS","startIndex":photo_col,"endIndex":photo_col+1},
            "properties":{"pixelSize":150},"fields":"pixelSize"
        }})
    if requests:
        for i in range(0,len(requests),400):
            sh.batch_update({"requests":requests[i:i+400]})

    report={
        "ok":len(errors)==0,
        "finished_at":datetime.now(timezone.utc).isoformat(),
        "webasyst_type":TYPE_NAME,
        "webasyst_type_id":type_id,
        "webasyst_products":len(products),
        "webasyst_unique_articles":len(seen_wa),
        "existing_articles_before":len(all_values)-1,
        "skipped_existing_article":skipped_existing,
        "duplicate_articles_in_webasyst":duplicate_wa,
        "new_articles_added":added,
        "ozon_rows_blue":len(ozon_articles),
        "webasyst_only_rows_light_blue":sum(1 for k in existing_rows if k not in ozon_articles),
        "errors":errors,
        "rule":"If article exists on Ozon => row blue. Webasyst-only => light blue. Webasyst upload type always NORDEN-100.",
        "main_photo_rule":"Column Основное фото displays IMAGE(). Raw URLs remain in JSON/source data."
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
    if errors: raise SystemExit(2)

if __name__=="__main__":
    main()
