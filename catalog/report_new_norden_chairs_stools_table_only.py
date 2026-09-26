#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, json, os, re, sys, unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"catalog"/"norden_new_chairs_stools_table_only_report.json"
SID=os.environ["CATALOG_SPREADSHEET_ID"].strip()
SHEET=os.environ.get("CATALOG_SHEET","Норден").strip()
SA=os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
CONF=str.maketrans({"а":"a","в":"b","с":"c","е":"e","н":"h","к":"k","м":"m","о":"o","р":"p","т":"t","х":"x","у":"y",
                    "А":"a","В":"b","С":"c","Е":"e","Н":"h","К":"k","М":"m","О":"o","Р":"p","Т":"t","Х":"x","У":"y"})
EXCL=("чехол","сменный чехол","подголовник","подлокотник","крестовина","газлифт","ролик","колеса","колесо","механизм","сиденье","спинка")

def load(path,name):
    p=ROOT/path
    spec=importlib.util.spec_from_file_location(name,p)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

MOD=load(Path("norden-kit")/"sync_norden_kit.py","norden_source_table_only")

def s(v): return str(v or "").strip()
def nt(v): return re.sub(r"\s+"," ",unicodedata.normalize("NFKC",s(v)).casefold()).strip()
def nc(v): return re.sub(r"[^0-9a-z]+","",unicodedata.normalize("NFKC",s(v)).translate(CONF).casefold())
def actual_name(v):
    n=nt(v).replace("ё","е")
    return ("кресл" in n or "стул" in n) and not n.startswith(EXCL)
def target_item(i):
    path=" > ".join(i.get("category_path") or []).casefold().replace("ё","е")
    return ("кресл" in path or "стул" in path) and actual_name(i.get("name"))

def likely_duplicate(item, rows):
    k=nc(item.get("article"))
    name=nt(item.get("name"))
    hits=[]
    for r in rows:
        y=nc(r.get("YML ID"))
        if y:
            if y==k:
                continue
            if min(len(y),len(k))>=6 and (y.startswith(k) or k.startswith(y)):
                hits.append({"row":r["_row"],"article":r.get("Артикул",""),"yml_id":r.get("YML ID",""),"name":r.get("Название",""),"reason":"YML prefix/truncation"})
                continue
        nm=nt(r.get("Название"))
        if nm and name and min(len(nm),len(name))>=20 and (nm in name or name in nm):
            hits.append({"row":r["_row"],"article":r.get("Артикул",""),"yml_id":r.get("YML ID",""),"name":r.get("Название",""),"reason":"name containment"})
    return hits[:5]

def main():
    # Use the complete Norden XML sources directly: this is read-only and much faster than paginated API.
    src,dups=MOD.source_from_xml(short=False)
    kind="xml-full+price"
    apierr=None
    if len(src)<1000:
        raise RuntimeError(f"Safety stop: Norden full catalog too small ({len(src)})")
    # Current availability: only the two working Norden warehouses used by the catalog.
    price_root=ET.fromstring(MOD.download_bytes(MOD.PRICE_XML_URL))
    stock_total={}
    for n in price_root.iter("Номенклатура"):
        article=s(n.findtext("Артикул"))
        if not article:
            continue
        total=0
        for st in n.findall("СвободныйОстаток"):
            wh=s(st.attrib.get("Склад"))
            if wh in ("Основной склад","Питер Основной склад"):
                total += MOD.parse_stock(st.text) or 0
        stock_total[nc(article)]=total

    targets=[]
    for a,i in src.items():
        if not target_item(i):
            continue
        x=dict(i)
        x["article"]=s(x.get("article") or a)
        x["stock_total"]=stock_total.get(nc(x["article"]),0)
        targets.append(x)
    if len(targets)<100:
        raise RuntimeError(f"Safety stop: chairs/stools scope too small ({len(targets)})")

    creds=json.loads(SA)
    gc=gspread.authorize(Credentials.from_service_account_info(
        creds,scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    ))
    ws=gc.open_by_key(SID).worksheet(SHEET)
    vals=ws.get_all_values()
    if not vals:
        raise RuntimeError("Норден sheet is empty")
    headers=list(vals[0])
    ix={h:i for i,h in enumerate(headers)}
    for req in ("Артикул","Название","YML ID"):
        if req not in ix:
            raise RuntimeError("Missing column: "+req)
    rows=[]
    exact=defaultdict(list)
    for rn,row in enumerate(vals[1:],2):
        d={h:(row[i] if i<len(row) else "") for h,i in ix.items()}
        d["_row"]=rn
        rows.append(d)
        if s(d.get("YML ID")):
            exact[nc(d["YML ID"])].append(d)

    truly_new=[]
    possible=[]
    exact_count=0
    ambiguous_exact=0
    for i in targets:
        k=nc(i["article"])
        matches=exact.get(k,[])
        if len(matches)==1:
            exact_count+=1
            continue
        if len(matches)>1:
            ambiguous_exact+=1
            possible.append({"supplier":i,"hits":[{"row":r["_row"],"article":r.get("Артикул",""),"yml_id":r.get("YML ID",""),"name":r.get("Название",""),"reason":"duplicate exact YML"} for r in matches[:5]]})
            continue
        hits=likely_duplicate(i,rows)
        if hits:
            possible.append({"supplier":i,"hits":hits})
            continue
        if int(i.get("stock_total") or 0) <= 0:
            continue
        truly_new.append({
            "yml_id":i["article"],
            "name":s(i.get("name")),
            "category_path":i.get("category_path") or [],
            "images":i.get("images") or [],
            "description":s(i.get("description")),
            "stock_total":i.get("stock_total",0),
            "source_item":i,
        })

    report={
      "ok":True,
      "mode":"READ_ONLY_COMPARE_FOR_TABLE_ONLY",
      "source_kind":kind,
      "source_api_error":apierr,
      "source_catalog":len(src),
      "source_duplicate_articles":len(dups),
      "chairs_stools_scope":len(targets),
      "catalog_rows":len(rows),
      "exact_existing":exact_count,
      "ambiguous_exact":ambiguous_exact,
      "possible_duplicates_count":len(possible),
      "new_count":len(truly_new),
      "new_items":truly_new,
      "possible_duplicates":possible,
      "safety":"No writes to Google Sheet, KIT, Webasyst, Ozon or Yandex are performed by this script."
    }
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"new_count":len(truly_new),"possible_duplicates":len(possible),"scope":len(targets)},ensure_ascii=False))

if __name__=="__main__":
    main()
