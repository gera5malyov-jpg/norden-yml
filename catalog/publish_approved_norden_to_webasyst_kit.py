#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/"catalog"/"norden_approved_publish_report.json"
TRIGGER=ROOT/"catalog"/"approved_norden_publish_trigger.txt"
SID=os.environ["CATALOG_SPREADSHEET_ID"].strip()
SHEET=os.environ.get("CATALOG_SHEET","Норден").strip()
SA=os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
APPROVAL="Проверено — загрузить в KIT/Webasyst"
LOADED="ЗАГРУЖЕН В WEBASYST И KIT"

def load(path,name):
    p=ROOT/path
    spec=importlib.util.spec_from_file_location(name,p)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

SYNC=load(Path("catalog")/"sync_norden_chairs_stools_6h.py","norden_approved_sync")
MOD=SYNC.MOD
BRIDGE=SYNC.BRIDGE
sys.path.insert(0,str(ROOT/"webasyst"))
from client import WebasystClient

def s(v): return str(v or "").strip()
def now(): return datetime.now(timezone.utc).isoformat()
def d(v):
    x=s(v).replace("\xa0","").replace(" ","").replace(",",".")
    try: return Decimal(x)
    except: return None
def truth(v): return s(v).casefold() in ("true","истина","да","yes","1")
def money(v):
    x=d(v)
    return "" if x is None else f"{x.quantize(Decimal('0.01')):.2f}"

def apply_mode():
    try:
        txt=TRIGGER.read_text(encoding="utf-8")
    except Exception:
        return False
    for line in txt.splitlines():
        if line.strip().casefold() in ("apply=1","apply=true","apply=yes"):
            return True
    return False

def sheet():
    creds=json.loads(SA)
    gc=gspread.authorize(Credentials.from_service_account_info(
        creds,scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    ))
    return gc.open_by_key(SID).worksheet(SHEET)

def exact_category_id(categories,path):
    by=defaultdict(list)
    for row in categories:
        title=s(row.get("title") or row.get("name"))
        if title:
            by[(s(row.get("parent_id")),MOD.norm_title(title))].append(row)
    parent=""
    for title in path:
        matches=by.get((parent,MOD.norm_title(title)),[])
        if len(matches)!=1:
            return "",{"title":title,"parent_id":parent,"matches":len(matches)}
        parent=s(matches[0].get("id"))
    return parent,None

def source_data():
    src,dups=MOD.source_from_xml(short=False)
    ps,pdups=SYNC.price_stock()
    return src,ps,dups,pdups

def row_dict(headers,row,rn):
    out={h:(row[i] if i<len(row) else "") for i,h in enumerate(headers)}
    out["_row"]=rn
    return out

def write_fields(ws,headers,rn,fields):
    idx={h:i+1 for i,h in enumerate(headers)}
    req=[]
    for key,value in fields.items():
        if key not in idx:
            continue
        req.append({"range":gspread.utils.rowcol_to_a1(rn,idx[key]),"values":[[value]]})
    if req:
        ws.batch_update(req,value_input_option="USER_ENTERED")

def wa_exact_by_sku(waby,article):
    return waby.get(SYNC.nc(article),[]) if article else []

def kit_exact_by_sku(kby,article):
    return kby.get(SYNC.nc(article),[]) if article else []

def main():
    apply=apply_mode()
    rep={
      "started_at":now(),"apply":apply,"mode":"APPROVED_ONLY","status":"ВЫПОЛНЯЕТСЯ",
      "rules":{
        "selection":"только строки с TRUE в колонке согласования и не со статусом ЗАГРУЖЕН В WEBASYST И KIT",
        "channels":["Webasyst","KIT"],
        "ozon_yandex":"НЕ ТРОГАТЬ",
        "webasyst_price":"закупка×1.23; зачеркнутая×1.65",
        "kit_price":"закупка×1.26; зачеркнутая×1.65",
        "kit_stock":"МСК=factual Norden МСК; СПБ привозной=тот же остаток, что МСК",
        "markdown":"УЦЕНКА запрещена"
      },
      "selected":[],"preflight_errors":[],"created":[],"skipped_loaded":[],"errors":[],"complete":False
    }
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    ws=sheet()
    vals=ws.get_all_values()
    if not vals: raise RuntimeError("Норден sheet is empty")
    headers=vals[0]
    hi={h:i for i,h in enumerate(headers)}
    need=["Артикул","Название","YML ID","Бренд","Тип Webasyst при загрузке","Закупка","Остаток","Статус источника",APPROVAL]
    miss=[x for x in need if x not in hi]
    if miss: raise RuntimeError("Missing sheet columns: "+", ".join(miss))

    rows=[row_dict(headers,r,rn) for rn,r in enumerate(vals[1:],2)]
    selected=[]
    for r in rows:
        if not truth(r.get(APPROVAL)): continue
        if s(r.get("Статус источника"))==LOADED:
            rep["skipped_loaded"].append({"row":r["_row"],"article":r.get("Артикул"),"yml_id":r.get("YML ID")})
            continue
        selected.append(r)

    src,ps,dups,pdups=source_data()
    wa=WebasystClient(min_request_interval=0.45)
    tid,wstock,waby,wapid=SYNC.wa_prepare(wa)
    kit=BRIDGE.KitClient()
    msk_id,spb_id,kby,cats,chars=SYNC.kit_prepare(kit)

    approved_paths={
      "chair":["Мебель","Компьютерная и офисная мебель","Кресла офисные и компьютерные","Офисные кресла"],
      "stool":["Мебель","Столы и стулья","Стулья"]
    }

    prepared=[]
    for r in selected:
        rn=r["_row"]; yml=s(r.get("YML ID")); name=s(r.get("Название")); article=s(r.get("Артикул"))
        item=src.get(yml)
        errs=[]
        if not yml: errs.append("пустой YML ID")
        if not item: errs.append("YML ID не найден в свежем Norden.xml")
        lower=(name+" "+s((item or {}).get("name"))).casefold().replace("ё","е")
        if "уценк" in lower: errs.append("товар содержит признак УЦЕНКА")
        kind="chair" if "кресл" in lower else ("stool" if "стул" in lower else "")
        if not kind: errs.append("товар не определён как кресло/стул")
        p=ps.get(SYNC.nc(yml),{}) if yml else {}
        msk=int(p.get("msk") or 0); spb=int(p.get("spb") or 0); total=msk+spb
        if total<=0: errs.append("остаток Norden <= 0")
        sheet_stock=d(r.get("Остаток"))
        if sheet_stock is None or int(sheet_stock)!=total:
            errs.append(f"остаток таблицы {r.get('Остаток')} != свежему Norden {total}")
        sheet_purchase=d(r.get("Закупка")); src_purchase=p.get("purchase")
        if sheet_purchase is None or sheet_purchase<=0:
            errs.append("нет положительной закупки в таблице")
        if src_purchase is None or Decimal(str(src_purchase))<=0:
            errs.append("нет положительной закупки в Norden")
        elif sheet_purchase is not None and sheet_purchase.quantize(Decimal("0.01")) != Decimal(str(src_purchase)).quantize(Decimal("0.01")):
            errs.append(f"закупка таблицы {sheet_purchase} != свежей Norden {src_purchase}")
        if s(r.get("Бренд")).casefold()!="norden": errs.append("бренд строки не Norden")
        if s(r.get("Тип Webasyst при загрузке"))!="NORDEN-100": errs.append("тип Webasyst не NORDEN-100")

        path=approved_paths.get(kind,[])
        cid,caterr=exact_category_id(cats,path) if path else ("",{"reason":"no path"})
        if caterr: errs.append("в KIT не найдена точная согласованная категория: "+" > ".join(path))

        wa_matches=wa_exact_by_sku(waby,article)
        kit_matches=kit_exact_by_sku(kby,article)
        if article and len(wa_matches)!=1:
            errs.append(f"для заполненного Артикула {article} в Webasyst найдено {len(wa_matches)} точных SKU")
        if article and len(kit_matches)>1:
            errs.append(f"для Артикула {article} в KIT найдено {len(kit_matches)} точных SKU")

        entry={
          "row":rn,"yml_id":yml,"name":name,"article":article,
          "purchase":money(sheet_purchase),"norden_msk":msk,"norden_spb":spb,"norden_total":total,
          "webasyst_sale":money(sheet_purchase*Decimal("1.23")) if sheet_purchase else "",
          "webasyst_old":money(sheet_purchase*Decimal("1.65")) if sheet_purchase else "",
          "kit_sale":money(sheet_purchase*Decimal("1.26")) if sheet_purchase else "",
          "kit_old":money(sheet_purchase*Decimal("1.65")) if sheet_purchase else "",
          "kit_category":" > ".join(path),"kit_category_id":cid,
          "existing_webasyst_sku_matches":len(wa_matches),"existing_kit_sku_matches":len(kit_matches),
          "errors":errs
        }
        rep["selected"].append(entry)
        if errs:
            rep["preflight_errors"].append({"row":rn,"yml_id":yml,"errors":errs})
        else:
            x=dict(item)
            x.update({
              "article":yml,
              "name":name,
              "purchase":sheet_purchase,
              "msk":msk,"spb":spb,"total":total,
              "_row":rn,"_article":article,"_category_path":path,"_category_id":cid
            })
            prepared.append(x)

    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if rep["preflight_errors"]:
        rep["status"]="ОШИБКА ПРЕДПРОВЕРКИ"
        rep["finished_at"]=now()
        REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(rep,ensure_ascii=False,indent=2))
        return 2

    if not apply:
        rep["status"]="ПРЕДПРОВЕРКА УСПЕШНА"
        rep["complete"]=True
        rep["finished_at"]=now()
        REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(rep,ensure_ascii=False,indent=2))
        return 0

    for item in prepared:
        rn=item["_row"]; yml=item["article"]; article=item["_article"]
        result={"row":rn,"yml_id":yml,"name":item["name"],"webasyst":{},"kit":{}}
        try:
            # Webasyst: create only when row has no internal Article.
            if not article:
                cr=SYNC.wa_create(wa,tid,wstock,item)
                article=cr["article"]
                result["webasyst"]={"action":"created","article":article,**cr}
                write_fields(ws,headers,rn,{
                  "Артикул":article,
                  "Webasyst product_id":cr["pid"],
                  "Webasyst sku_id":cr["sid"],
                  "Цена Webasyst":money(item["purchase"]*Decimal("1.23")),
                  "Старая цена Webasyst":money(item["purchase"]*Decimal("1.65")),
                  "Закупочная цена Webasyst":money(item["purchase"]),
                  "Тип Webasyst":"NORDEN-100",
                  "Остаток Webasyst":item["total"],
                  "Статус Webasyst":"СОЗДАН",
                  "Дата Webasyst":now()
                })
                # Refresh in-memory exact SKU index for this run.
                waby[SYNC.nc(article)].append(({"id":cr["pid"]},{"id":cr["sid"],"sku":article}))
            else:
                matches=wa_exact_by_sku(waby,article)
                result["webasyst"]={"action":"existing","article":article,"matches":len(matches)}

            # KIT: exact same internal Article/SKU; never create a second exact SKU.
            km=kit_exact_by_sku(kby,article)
            if len(km)==1:
                full=kit.request("GET",f"/v1/variants/{s(km[0].get('id'))}")
                vid=s(km[0].get("id")); pid=s(full.get("product_id") or km[0].get("product_id")); kid=full.get("kit_id")
                result["kit"]={"action":"existing","variant_id":vid,"product_id":pid,"kit_id":kid}
            elif len(km)>1:
                raise RuntimeError(f"KIT: duplicate exact SKU {article}: {len(km)}")
            else:
                vid=SYNC.kit_create(kit,article,item,item["_category_path"],msk_id,spb_id,cats,chars)
                full=kit.request("GET",f"/v1/variants/{vid}")
                pid=s(full.get("product_id")); kid=full.get("kit_id")
                result["kit"]={"action":"created","variant_id":vid,"product_id":pid,"kit_id":kid}
                kby[SYNC.nc(article)].append(full if isinstance(full,dict) else {"id":vid,"sku":article})

            # Read-back verification.
            wa_m=wa_exact_by_sku(waby,article)
            full=kit.request("GET",f"/v1/variants/{result['kit']['variant_id']}")
            got_stocks={}
            for st in full.get("stocks") or []:
                if isinstance(st,dict):
                    got_stocks[s(st.get("warehouse_id"))]=int(float(st.get("quantity") or 0))
            prices=full.get("pricing") or {}
            expected_old=money(item["purchase"]*Decimal("1.65"))
            expected_sale=money(item["purchase"]*Decimal("1.26"))
            result["verify"]={
              "webasyst_exact_sku_matches":len(wa_m),
              "kit_sku":s(full.get("sku")),
              "kit_msk":got_stocks.get(msk_id),
              "kit_spb_transfer":got_stocks.get(spb_id),
              "kit_price":s(prices.get("price")),
              "kit_manual_discount_price":s(prices.get("manual_discount_price"))
            }
            if len(wa_m)!=1: raise RuntimeError(f"Webasyst readback SKU matches={len(wa_m)}")
            if s(full.get("sku"))!=article: raise RuntimeError("KIT readback SKU mismatch")
            if got_stocks.get(msk_id)!=item["msk"] or got_stocks.get(spb_id)!=item["msk"]:
                raise RuntimeError(f"KIT stock readback mismatch: {got_stocks}")
            if d(prices.get("price")) != d(expected_old) or d(prices.get("manual_discount_price")) != d(expected_sale):
                raise RuntimeError(f"KIT price readback mismatch: {prices}")

            write_fields(ws,headers,rn,{
              "KIT product_id":result["kit"]["product_id"],
              "KIT variant_id":result["kit"]["variant_id"],
              "KIT ID":result["kit"]["kit_id"] if result["kit"]["kit_id"] is not None else "",
              "KIT Цена до скидки":expected_old,
              "KIT Цена со скидкой":expected_sale,
              "Статус источника":LOADED
            })
            rep["created"].append(result)
            REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        except Exception as exc:
            result["error"]=str(exc)[:1500]
            rep["errors"].append(result)
            REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    rep["complete"]=not rep["errors"]
    rep["status"]="УСПЕШНО" if rep["complete"] else "ЗАВЕРШЕНО С ОШИБКАМИ"
    rep["finished_at"]=now()
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(rep,ensure_ascii=False,indent=2))
    return 0 if rep["complete"] else 2

if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        rep={"started_at":now(),"finished_at":now(),"status":"ОШИБКА","complete":False,"fatal_error":str(exc)}
        REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(rep,ensure_ascii=False,indent=2))
        raise
