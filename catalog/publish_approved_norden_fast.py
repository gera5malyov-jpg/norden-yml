#!/usr/bin/env python3
from __future__ import annotations

import importlib.util, json, os, sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/"catalog"/"norden_approved_publish_fast_report.json"
TRIGGER=ROOT/"catalog"/"approved_norden_publish_fast_trigger.txt"
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

SYNC=load(Path("catalog")/"sync_norden_chairs_stools_6h.py","norden_fast_sync")
MOD=SYNC.MOD
BRIDGE=SYNC.BRIDGE
sys.path.insert(0,str(ROOT/"webasyst"))
from client import WebasystClient

def s(v): return str(v or "").strip()
def now(): return datetime.now(timezone.utc).isoformat()
def dec(v):
    x=s(v).replace("\xa0","").replace(" ","").replace(",",".")
    try: return Decimal(x)
    except: return None
def truth(v): return s(v).casefold() in ("true","истина","да","yes","1")
def money(v):
    x=dec(v)
    return "" if x is None else f"{x.quantize(Decimal('0.01')):.2f}"
def apply_mode():
    try: txt=TRIGGER.read_text(encoding="utf-8")
    except Exception: return False
    return any(line.strip().casefold() in ("apply=1","apply=true","apply=yes") for line in txt.splitlines())

def get_ws():
    cr=json.loads(SA)
    gc=gspread.authorize(Credentials.from_service_account_info(
        cr,scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    ))
    return gc.open_by_key(SID).worksheet(SHEET)

def write_fields(ws,headers,rn,fields):
    idx={h:i+1 for i,h in enumerate(headers)}
    req=[]
    for k,v in fields.items():
        if k in idx:
            req.append({"range":gspread.utils.rowcol_to_a1(rn,idx[k]),"values":[[v]]})
    if req:
        ws.batch_update(req,value_input_option="USER_ENTERED")

def exact_category_id(categories,path):
    by=defaultdict(list)
    for row in categories:
        title=s(row.get("title") or row.get("name"))
        if title:
            by[(s(row.get("parent_id")),MOD.norm_title(title))].append(row)
    parent=""
    trace=[]
    for title in path:
        matches=by.get((parent,MOD.norm_title(title)),[])
        trace.append({"title":title,"parent_id":parent,"matches":len(matches)})
        if len(matches)!=1:
            return "",trace
        parent=s(matches[0].get("id"))
    return parent,trace

def kit_exact_sku(kit,article):
    if not article: return []
    payload=kit.request("GET","/v1/variants",params={"name":article,"page":1,"per_page":100})
    return [x for x in kit.items(payload) if s(x.get("sku"))==article]

def extract_stocks(full):
    out={}
    raw=full.get("stocks") if isinstance(full,dict) else None
    if isinstance(raw,dict):
        for k,v in raw.items():
            if isinstance(v,dict):
                wid=s(v.get("warehouse_id") or k)
                out[wid]=int(float(v.get("quantity") or v.get("stock") or 0))
            else:
                out[s(k)]=int(float(v or 0))
    elif isinstance(raw,list):
        for v in raw:
            if isinstance(v,dict):
                wid=s(v.get("warehouse_id") or v.get("id"))
                if wid: out[wid]=int(float(v.get("quantity") or v.get("stock") or 0))
    return out

def main():
    apply=apply_mode()
    rep={"started_at":now(),"apply":apply,"mode":"APPROVED_ONLY_FAST","status":"ВЫПОЛНЯЕТСЯ",
         "selected":[],"preflight_errors":[],"created":[],"skipped_loaded":[],"errors":[],"complete":False,
         "rules":{"scope":"только отмеченные TRUE","channels":["Webasyst","KIT"],"ozon_yandex":"НЕ ТРОГАТЬ",
                  "webasyst_price":"закупка×1.23; зачеркнутая×1.65",
                  "kit_price":"закупка×1.26; зачеркнутая×1.65",
                  "kit_stock":"МСК=остаток Norden МСК; СПБ привозной=тот же остаток",
                  "markdown":"УЦЕНКА запрещена"}}
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    ws=get_ws()
    vals=ws.get_all_values()
    headers=vals[0]
    hi={h:i for i,h in enumerate(headers)}
    required=["Артикул","Название","YML ID","Бренд","Тип Webasyst при загрузке","Закупка","Остаток","Статус источника",APPROVAL]
    missing=[x for x in required if x not in hi]
    if missing: raise RuntimeError("Missing columns: "+", ".join(missing))

    selected=[]
    for rn,row in enumerate(vals[1:],2):
        def cell(h): return row[hi[h]] if hi[h]<len(row) else ""
        if not truth(cell(APPROVAL)): continue
        if s(cell("Статус источника"))==LOADED:
            rep["skipped_loaded"].append({"row":rn,"article":cell("Артикул"),"yml_id":cell("YML ID")})
            continue
        selected.append({"_row":rn,**{h:cell(h) for h in headers}})

    source,_=MOD.source_from_xml(short=False)
    ps,_=SYNC.price_stock()

    wa=WebasystClient(min_request_interval=0.45)
    types=SYNC.listify(wa.call("shop.type.getList"))
    tm=[x for x in types if SYNC.nt(x.get("name") or x.get("title"))==SYNC.nt("NORDEN-100")]
    stocks=SYNC.listify(wa.call("shop.stock.getList"))
    sm=[x for x in stocks if SYNC.nt(x.get("name") or x.get("title"))==SYNC.nt("Основной склад")]
    if len(tm)!=1: raise RuntimeError(f"Webasyst type NORDEN-100: found {len(tm)}")
    if len(sm)!=1: raise RuntimeError(f"Webasyst stock Основной склад: found {len(sm)}")
    type_id=s(tm[0].get("id")); wa_stock_id=s(sm[0].get("id"))

    kit=BRIDGE.KitClient()
    warehouses=kit.warehouses()
    wh={SYNC.nt(x.get("title") or x.get("name")):s(x.get("id")) for x in warehouses}
    msk_id=wh.get(SYNC.nt("МСК")); spb_id=wh.get(SYNC.nt("СПБ привозной"))
    if not msk_id or not spb_id: raise RuntimeError("KIT warehouses МСК / СПБ привозной not found")
    cats=kit.categories()
    chars=kit.characteristics()

    paths={
      "chair":["Мебель","Компьютерная и офисная мебель","Кресла офисные и компьютерные","Офисные кресла"],
      "stool":["Мебель","Столы и стулья","Стулья"]
    }

    prepared=[]
    for r in selected:
        rn=r["_row"]; yml=s(r.get("YML ID")); name=s(r.get("Название")); article=s(r.get("Артикул"))
        errs=[]
        item=source.get(yml)
        if not item: errs.append("YML ID не найден в свежем Norden.xml")
        lower=(name+" "+s((item or {}).get("name"))).casefold().replace("ё","е")
        if "уценк" in lower: errs.append("УЦЕНКА запрещена")
        kind="chair" if "кресл" in lower else ("stool" if "стул" in lower else "")
        if not kind: errs.append("не кресло/стул")

        p=ps.get(SYNC.nc(yml),{})
        msk=int(p.get("msk") or 0); spb=int(p.get("spb") or 0); total=msk+spb
        if total<=0: errs.append("остаток <= 0")
        sheet_stock=dec(r.get("Остаток"))
        if sheet_stock is None or int(sheet_stock)!=total:
            errs.append(f"остаток таблицы {r.get('Остаток')} != Norden {total}")
        purchase=dec(r.get("Закупка")); live_purchase=p.get("purchase")
        if purchase is None or purchase<=0: errs.append("нет положительной закупки")
        if live_purchase is None:
            errs.append("нет закупки в Norden")
        elif purchase is not None and purchase.quantize(Decimal("0.01"))!=Decimal(str(live_purchase)).quantize(Decimal("0.01")):
            errs.append(f"закупка таблицы {purchase} != Norden {live_purchase}")
        if s(r.get("Бренд")).casefold()!="norden": errs.append("бренд не Norden")
        if s(r.get("Тип Webasyst при загрузке"))!="NORDEN-100": errs.append("тип Webasyst не NORDEN-100")

        path=paths.get(kind,[])
        cid,trace=exact_category_id(cats,path) if path else ("",[])
        if not cid: errs.append("точная категория KIT не найдена: "+" > ".join(path))

        kit_hits=kit_exact_sku(kit,article) if article else []
        if article and len(kit_hits)>1: errs.append(f"KIT exact SKU duplicates={len(kit_hits)}")

        e={"row":rn,"yml_id":yml,"name":name,"article":article,"purchase":money(purchase),
           "norden_msk":msk,"norden_spb":spb,"norden_total":total,
           "webasyst_sale":money(purchase*Decimal("1.23")) if purchase else "",
           "webasyst_old":money(purchase*Decimal("1.65")) if purchase else "",
           "kit_sale":str(MOD.ceil_rub(purchase*Decimal("1.26"))) if purchase else "",
           "kit_old":str(MOD.ceil_rub(purchase*Decimal("1.65"))) if purchase else "",
           "kit_category":" > ".join(path),"kit_category_id":cid,
           "kit_exact_sku_matches":len(kit_hits),"errors":errs}
        rep["selected"].append(e)
        if errs:
            rep["preflight_errors"].append({"row":rn,"yml_id":yml,"errors":errs})
        else:
            x=dict(item)
            x.update({"article":yml,"name":name,"purchase":purchase,"msk":msk,"spb":spb,"total":total,
                      "_row":rn,"_article":article,"_path":path})
            prepared.append(x)

    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if rep["preflight_errors"]:
        rep["status"]="ОШИБКА ПРЕДПРОВЕРКИ"; rep["finished_at"]=now()
        REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(rep,ensure_ascii=False,indent=2)); return 2

    if not apply:
        rep["status"]="ПРЕДПРОВЕРКА УСПЕШНА"; rep["complete"]=True; rep["finished_at"]=now()
        REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(rep,ensure_ascii=False,indent=2)); return 0

    for item in prepared:
        rn=item["_row"]; yml=item["article"]; article=item["_article"]
        result={"row":rn,"yml_id":yml,"name":item["name"],"webasyst":{},"kit":{}}
        try:
            if article:
                raise RuntimeError(f"Строка уже имеет Артикул {article}, но статус ещё не {LOADED}; автоматическое повторное создание остановлено")

            cr=SYNC.wa_create(wa,type_id,wa_stock_id,item)
            article=cr["article"]
            result["webasyst"]={"action":"created","article":article,"product_id":cr["pid"],"sku_id":cr["sid"]}
            write_fields(ws,headers,rn,{
              "Артикул":article,"Webasyst product_id":cr["pid"],"Webasyst sku_id":cr["sid"],
              "Цена Webasyst":money(item["purchase"]*Decimal("1.23")),
              "Старая цена Webasyst":money(item["purchase"]*Decimal("1.65")),
              "Закупочная цена Webasyst":money(item["purchase"]),
              "Тип Webasyst":"NORDEN-100","Остаток Webasyst":item["total"],
              "Статус Webasyst":"СОЗДАН","Дата Webasyst":now()
            })
            REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

            hits=kit_exact_sku(kit,article)
            if hits:
                raise RuntimeError(f"KIT exact SKU {article} already exists: {len(hits)}")

            vid=SYNC.kit_create(kit,article,item,item["_path"],msk_id,spb_id,cats,chars)
            full=kit.request("GET",f"/v1/variants/{vid}")
            pid=s(full.get("product_id")); kid=full.get("kit_id")
            result["kit"]={"action":"created","variant_id":vid,"product_id":pid,"kit_id":kid}

            # Verify Webasyst SKU.
            wa_skus=SYNC.listify(wa.call("shop.product.skus.getList",params={"product_id":cr["pid"]}),("skus","items"))
            exact_wa=[x for x in wa_skus if s(x.get("sku"))==article]
            if len(exact_wa)!=1: raise RuntimeError(f"Webasyst SKU readback mismatch: {len(exact_wa)}")

            # Verify KIT SKU, stock and prices.
            full=kit.request("GET",f"/v1/variants/{vid}")
            if s(full.get("sku"))!=article: raise RuntimeError("KIT SKU readback mismatch")
            got=extract_stocks(full)
            if got.get(msk_id)!=item["msk"] or got.get(spb_id)!=item["msk"]:
                raise RuntimeError(f"KIT stock mismatch: {got}")
            price_obj=full.get("pricing") if isinstance(full.get("pricing"),dict) else full
            exp_old=str(MOD.ceil_rub(item["purchase"]*Decimal("1.65")))
            exp_sale=str(MOD.ceil_rub(item["purchase"]*Decimal("1.26")))
            if dec(price_obj.get("price"))!=dec(exp_old) or dec(price_obj.get("manual_discount_price"))!=dec(exp_sale):
                raise RuntimeError(f"KIT price mismatch: price={price_obj.get('price')} sale={price_obj.get('manual_discount_price')}")

            result["verify"]={"webasyst_sku":article,"kit_sku":s(full.get("sku")),
                              "kit_msk":got.get(msk_id),"kit_spb_transfer":got.get(spb_id),
                              "kit_old":s(price_obj.get("price")),"kit_sale":s(price_obj.get("manual_discount_price"))}
            write_fields(ws,headers,rn,{
              "KIT product_id":pid,"KIT variant_id":vid,"KIT ID":kid if kid is not None else "",
              "KIT Цена до скидки":exp_old,"KIT Цена со скидкой":exp_sale,
              "Статус источника":LOADED
            })
            rep["created"].append(result)
            REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        except Exception as exc:
            result["error"]=str(exc)[:1500]; rep["errors"].append(result)
            REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    rep["complete"]=not rep["errors"]
    rep["status"]="УСПЕШНО" if rep["complete"] else "ЗАВЕРШЕНО С ОШИБКАМИ"
    rep["finished_at"]=now()
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(rep,ensure_ascii=False,indent=2))
    return 0 if rep["complete"] else 2

if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        rep={"started_at":now(),"finished_at":now(),"status":"ОШИБКА","complete":False,"fatal_error":str(exc)}
        REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(rep,ensure_ascii=False,indent=2))
        raise
