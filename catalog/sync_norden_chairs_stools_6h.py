#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, json, os, re, sys, time, unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import gspread, requests
from google.oauth2.service_account import Credentials

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/"catalog"/"norden_chairs_stools_6h_report.json"
SID=os.environ["CATALOG_SPREADSHEET_ID"].strip()
SHEET=os.environ.get("CATALOG_SHEET","Норден").strip()
REVIEW=os.environ.get("NORDEN_CATEGORY_REVIEW_SHEET","Норден — категории на согласование").strip()
SA=os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
TYPE="NORDEN-100"; WA_STOCK="Основной склад"; KIT_MSK="МСК"; KIT_SPB="СПБ привозной"
PRICE_XML="https://norden.group/index.php?dispatch=sw_user_prices.get_file&file=Norden.group+-K8%25.xml"
MONEY=Decimal("0.01")
CONF=str.maketrans({"а":"a","в":"b","с":"c","е":"e","н":"h","к":"k","м":"m","о":"o","р":"p","т":"t","х":"x","у":"y",
                    "А":"a","В":"b","С":"c","Е":"e","Н":"h","К":"k","М":"m","О":"o","Р":"p","Т":"t","Х":"x","У":"y"})
EXCL=("чехол","сменный чехол","подголовник","подлокотник","крестовина","газлифт","ролик","колеса","колесо","механизм","сиденье","спинка")
REQ=["Артикул","Название","YML ID","Бренд","Основное фото","Фото","Источник","Webasyst product_id","Webasyst sku_id",
     "Тип Webasyst при загрузке","Webasyst URL","Цена Webasyst","Старая цена Webasyst","Закупочная цена Webasyst",
     "Закупка","РРЦ поставщика","Остаток"]

def load(path,name):
    p=ROOT/path; spec=importlib.util.spec_from_file_location(name,p); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
MOD=load(Path("norden-kit")/"sync_norden_kit.py","norden_sync_6h")
BRIDGE=load(Path("norden-kit")/"webasyst_n100_to_kit_once.py","norden_bridge_6h")
sys.path.insert(0,str(ROOT/"webasyst"))
from client import WebasystClient

def s(v): return str(v or "").strip()
def nt(v): return re.sub(r"\s+"," ",unicodedata.normalize("NFKC",s(v)).casefold()).strip()
def nc(v): return re.sub(r"[^0-9a-z]+","",unicodedata.normalize("NFKC",s(v)).translate(CONF).casefold())
def now(): return datetime.now(timezone.utc).isoformat()
def num(v):
    x=s(v).replace("\xa0"," ").replace(" ","").replace(",",".")
    if not x: return None
    try: return Decimal(x)
    except (InvalidOperation,ValueError): return None
def q(v):
    d=num(v)
    return 0 if d is None else max(0,int(d))
def rub(v):
    d=num(v)
    return None if d is None else d.quantize(MONEY,rounding=ROUND_HALF_UP)
def price(p,m):
    p=rub(p); return None if p is None or p<=0 else (p*Decimal(m)).quantize(MONEY,rounding=ROUND_HALF_UP)
def ms(v):
    d=rub(v); return "" if d is None else f"{d:.2f}"
def listify(x,keys=()):
    if isinstance(x,list): return [a for a in x if isinstance(a,dict)]
    if not isinstance(x,dict): return []
    for k in keys:
        v=x.get(k)
        if isinstance(v,list): return [a for a in v if isinstance(a,dict)]
        if isinstance(v,dict): return [a for a in v.values() if isinstance(a,dict)]
    return []
def skus(p):
    v=p.get("skus")
    return [x for x in (list(v.values()) if isinstance(v,dict) else v or []) if isinstance(x,dict)]
def actual_name(v):
    n=nt(v).replace("ё","е")
    return ("кресл" in n or "стул" in n) and not n.startswith(EXCL)
def target_item(i):
    path=" > ".join(i.get("category_path") or []).casefold().replace("ё","е")
    return ("кресл" in path or "стул" in path) and actual_name(i.get("name"))
def imgf(u): return '=IMAGE("'+s(u).replace('"','""')+'")' if s(u) else ""
def extimgs(urls): return "\n".join(f"[extimg]\n{s(u)}\n[/extimg]" for u in dict.fromkeys(urls or []) if s(u))

def price_stock():
    r=requests.get(PRICE_XML,headers={"User-Agent":"Mozilla/5.0"},timeout=180); r.raise_for_status()
    root=ET.fromstring(r.content); out={}; dup=[]
    for n in root.iter("Номенклатура"):
        a=s(n.findtext("Артикул"))
        if not a: continue
        prices={s(x.attrib.get("ВидЦен")):num(x.text) for x in n.findall("Цена")}
        stocks={s(x.attrib.get("Склад")):q(x.text) for x in n.findall("СвободныйОстаток")}
        k=nc(a)
        if k in out: dup.append(a)
        msk=stocks.get("Основной склад",0); spb=stocks.get("Питер Основной склад",0)
        out[k]={"purchase":prices.get("Опт"),"rrp":prices.get("РРЦ"),"msk":msk,"spb":spb,"total":msk+spb}
    if len(out)<1000: raise RuntimeError(f"Safety stop: Norden price feed too small ({len(out)})")
    return out,dup

def supplier():
    src,dups,kind,apierr=MOD.load_source(os.environ.get("NORDEN_SECRET",""),short=False)
    if len(src)<1000: raise RuntimeError(f"Safety stop: Norden full catalog too small ({len(src)})")
    ps,pdups=price_stock(); out={}
    for a,i in src.items():
        if not target_item(i): continue
        x=dict(i); p=ps.get(nc(a),{})
        x.update({"purchase":p.get("purchase"),"rrp":p.get("rrp"),"msk":p.get("msk",0),"spb":p.get("spb",0),"total":p.get("total",0)})
        out[nc(a)]=x
    if len(out)<100: raise RuntimeError(f"Safety stop: chairs/stools scope too small ({len(out)})")
    return out,{"catalog":len(src),"target":len(out),"source":kind,"api_error":apierr,"source_duplicates":len(dups),"price_duplicates":len(pdups)}

def sheets():
    cr=json.loads(SA); gc=gspread.authorize(Credentials.from_service_account_info(cr,scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]))
    sh=gc.open_by_key(SID); ws=sh.worksheet(SHEET); return sh,ws

def read(ws):
    vals=ws.get_all_values()
    if not vals: raise RuntimeError("Норден sheet is empty")
    h=list(vals[0])
    for x in REQ:
        if x not in h: h.append(x)
    if ws.col_count<len(h): ws.resize(cols=len(h))
    ws.update(range_name=f"A1:{gspread.utils.rowcol_to_a1(1,len(h))}",values=[h],value_input_option="RAW")
    ix={x:i for i,x in enumerate(h)}; rows=[]
    for rn,v in enumerate(vals[1:],2):
        d={x:(v[i] if i<len(v) else "") for x,i in ix.items()}; d["_row"]=rn; rows.append(d)
    return h,ix,rows

def cells(ws,ix,changes):
    req=[]
    for rn,col,val,ue in changes:
        req.append((ue,{"range":gspread.utils.rowcol_to_a1(rn,ix[col]+1),"values":[[val]]}))
    for ue in (False,True):
        a=[x for flag,x in req if flag==ue]
        for i in range(0,len(a),300): ws.batch_update(a[i:i+300],value_input_option="USER_ENTERED" if ue else "RAW")

def likely_duplicate(item,rows):
    k=nc(item["article"]); name=nt(item["name"])
    hits=[]
    for r in rows:
        y=nc(r.get("YML ID"))
        if y and y!=k and min(len(y),len(k))>=6 and (y.startswith(k) or k.startswith(y)): hits.append({"row":r["_row"],"yml":r.get("YML ID"),"name":r.get("Название")})
        nm=nt(r.get("Название"))
        if nm and min(len(nm),len(name))>=20 and (nm in name or name in nm): hits.append({"row":r["_row"],"yml":r.get("YML ID"),"name":r.get("Название")})
    return hits[:5]

def wa_prepare(wa):
    types=listify(wa.call("shop.type.getList"))
    tm=[x for x in types if nt(x.get("name") or x.get("title"))==nt(TYPE)]
    if len(tm)!=1: raise RuntimeError(f"Webasyst type {TYPE}: found {len(tm)}")
    stocks=listify(wa.call("shop.stock.getList"))
    sm=[x for x in stocks if nt(x.get("name") or x.get("title"))==nt(WA_STOCK)]
    if len(sm)!=1: raise RuntimeError(f"Webasyst stock {WA_STOCK}: found {len(sm)}")
    tid=s(tm[0].get("id")); sid=s(sm[0].get("id")); products=[]; off=0
    while True:
        p=wa.call("shop.product.search",params={"hash":f"type/{tid}","offset":off,"limit":1000,"fields":"*,skus,stock_counts"})
        b=listify(p,("products","items")); products+=b
        if not b or len(b)<1000: break
        off+=len(b)
    by=defaultdict(list); bypid={}
    for p in products:
        if s(p.get("id")): bypid[s(p["id"])]=p
        for x in skus(p):
            if s(x.get("sku")): by[nc(x["sku"])].append((p,x))
    return tid,sid,by,bypid

def product_id(x):
    if isinstance(x,dict):
        for k in ("id","product_id"):
            if s(x.get(k)): return s(x[k])
        if isinstance(x.get("product"),dict): return s(x["product"].get("id"))
    return ""

def wa_update(wa,stock_id,sku_row,item):
    data={"stock":{stock_id:str(item["total"] if item else 0)}}
    if item and rub(item.get("purchase")) and rub(item["purchase"])>0:
        p=item["purchase"]; data.update({"purchase_price":ms(p),"price":ms(price(p,"1.23")),"compare_price":ms(price(p,"1.65"))})
    wa.call("shop.product.skus.update",http_method="POST",params={"id":s(sku_row.get("id"))},data=data)
    return data

def wa_create(wa,type_id,stock_id,item):
    if not rub(item.get("purchase")) or rub(item["purchase"])<=0: raise RuntimeError("Нет положительной закупочной цены")
    p=item["purchase"]
    cr=wa.call("shop.product.add",http_method="POST",data={"name":item["name"],"type_id":type_id,"currency":"RUB","summary":extimgs(item.get("images")),"description":s(item.get("description")),"status":1,
        "skus":[{"price":ms(price(p,"1.23")),"compare_price":ms(price(p,"1.65")),"purchase_price":ms(p),"stock":{stock_id:str(item["total"])},"available":1,"status":1}]})
    pid=product_id(cr)
    if not pid: raise RuntimeError("Webasyst did not return product_id")
    ls=listify(wa.call("shop.product.skus.getList",params={"product_id":pid}),("skus","items"))
    if len(ls)!=1: raise RuntimeError(f"Webasyst product {pid}: {len(ls)} SKU")
    x=ls[0]; article=s(x.get("sku"))
    if not article: raise RuntimeError(f"Webasyst did not generate SKU for product {pid}")
    wa_update(wa,stock_id,x,item)
    info=wa.call("shop.product.getInfo",params={"id":pid})
    url=s(info.get("frontend_url") or info.get("url")); url=(os.environ.get("WEBASYST_BASE_URL") or "https://profikompany.ru").rstrip("/")+"/"+url.lstrip("/") if url and not url.startswith("http") else url
    return {"article":article,"pid":pid,"sid":s(x.get("id")),"url":url}

def kit_prepare(kit):
    wh={nt(x.get("title") or x.get("name")):s(x.get("id")) for x in kit.warehouses()}
    if nt(KIT_MSK) not in wh or nt(KIT_SPB) not in wh: raise RuntimeError("KIT warehouses МСК / СПБ привозной not found")
    by=defaultdict(list)
    for x in kit.scan_all_variants_parallel(workers=8):
        if s(x.get("sku")): by[nc(x["sku"])].append(x)
    return wh[nt(KIT_MSK)],wh[nt(KIT_SPB)],by,kit.categories(),kit.characteristics()

def kit_stock_price(kit,row,item,msk_id,spb_id):
    vid=s(row.get("id"))
    stocks=[{"variant_id":vid,"warehouse_id":msk_id,"quantity":item["msk"] if item else 0},{"variant_id":vid,"warehouse_id":spb_id,"quantity":item["spb"] if item else 0}]
    kit.request("POST","/v1/variants/stocks/bulk_update",body={"items":stocks})
    if item and rub(item.get("purchase")) and rub(item["purchase"])>0:
        p=item["purchase"]
        kit.request("POST","/v1/variants/prices/bulk_update",body={"items":[{"variant_id":vid,"price":ms(price(p,"1.65")),"manual_discount_price":ms(price(p,"1.26"))}]})

def kit_create(kit,article,item,path,msk_id,spb_id,cats,chars):
    cid=MOD.ensure_category_path(kit,cats,path); prod=kit.create_product(cid); pid=s(prod.get("id"))
    body={"sku":article,"name":item["name"],"status":"PUBLISHED","product_id":pid,"brand":"Norden","stocks":[{"warehouse_id":msk_id,"quantity":item["msk"],"reserved":0},{"warehouse_id":spb_id,"quantity":item["spb"],"reserved":0}]}
    if rub(item.get("purchase")) and rub(item["purchase"])>0:
        p=item["purchase"]; body["pricing"]={"price":ms(price(p,"1.65")),"manual_discount_price":ms(price(p,"1.26"))}
    v=kit.create_variant(body); vid=s(v.get("id"))
    if not vid: raise RuntimeError("KIT create returned no variant id")
    bytitle=defaultdict(list)
    for x in chars:
        if s(x.get("title")): bytitle[MOD.norm_title(x["title"])].append(x)
    code=MOD.characteristic_id(kit,chars,bytitle,MOD.CODE_SITE_TITLE)
    art=MOD.characteristic_id(kit,chars,bytitle,MOD.ARTICLE_TITLE)
    desired=MOD.build_source_characteristics(item,kit,chars,bytitle,code,art,article)
    patch={"characteristics":desired}
    if s(item.get("description")): patch["description"]=s(item["description"])
    media=[]
    for u in (item.get("images") or [])[:20]:
        try:
            up=kit.upload_image_url(u); fid=s(up.get("id"))
            if fid: media.append({"type":"IMAGE","display_sequence":len(media),"image_id":fid})
        except Exception: pass
    if media: patch["media"]=media
    kit.patch_variant(vid,patch)
    return vid

def review_sheet(sh,items):
    try: ws=sh.worksheet(REVIEW)
    except gspread.WorksheetNotFound: ws=sh.add_worksheet(title=REVIEW,rows=500,cols=7)
    ws.update("A1:G1",[["YML ID / артикул Norden","Название","Категория Norden","Статус","Категория KIT для согласования","Дата","Комментарий"]],value_input_option="RAW")
    vals=ws.get_all_values(); known={nc(r[0]):i+2 for i,r in enumerate(vals[1:]) if r and s(r[0])}
    add=[]
    for x in items:
        row=[x["article"],x["name"]," > ".join(x.get("category_path") or []),"НА СОГЛАСОВАНИЕ","",now(),""]
        if nc(x["article"]) in known: ws.update(f"A{known[nc(x['article'])]}:F{known[nc(x['article'])]}",[row[:6]],value_input_option="RAW")
        else: add.append(row)
    if add: ws.append_rows(add,value_input_option="RAW")

def main():
    rep={"started_at":now(),"status":"ВЫПОЛНЯЕТСЯ","source":{},"sheet":{"matched":0,"added":0,"zeroed_missing":0,"ambiguous_yml":0,"possible_duplicates":0},
         "webasyst":{"updated":0,"created":0,"zeroed_missing":0,"ambiguous":0,"errors":[]},"kit":{"updated":0,"created":0,"zeroed_missing":0,"ambiguous":0,"category_review":0,"errors":[]},
         "possible_duplicate_items":[],"category_review_items":[],"complete":False,
         "rules":{"scope":"только кресла/стулья","schedule":"каждые 6 часов","missing":"остаток 0, цены сохранять","webasyst_price":"закупка×1.23; зачеркнутая×1.65","kit_price":"закупка×1.26; зачеркнутая×1.65","old_norden_workflows":"STOP_ALL_NORDEN сохраняется"}}
    src,meta=supplier(); rep["source"]=meta
    sh,ws=sheets(); h,ix,rows=read(ws); by=defaultdict(list)
    for r in rows:
        if s(r.get("YML ID")): by[nc(r["YML ID"])].append(r)
    changes=[]; matched=set()
    for k,i in src.items():
        m=by.get(k,[])
        if len(m)==1:
            matched.add(k); rep["sheet"]["matched"]+=1; r=m[0]
            if i.get("purchase") is not None: changes.append((r["_row"],"Закупка",ms(i["purchase"]),False))
            if i.get("rrp") is not None: changes.append((r["_row"],"РРЦ поставщика",ms(i["rrp"]),False))
            changes.append((r["_row"],"Остаток",i["total"],False))
        elif len(m)>1:
            rep["sheet"]["ambiguous_yml"]+=1
    for r in rows:
        k=nc(r.get("YML ID"))
        if k and k not in src and actual_name(r.get("Название")):
            changes.append((r["_row"],"Остаток",0,False)); rep["sheet"]["zeroed_missing"]+=1
    cells(ws,ix,changes)
    new=[]
    for k,i in src.items():
        if k in matched or by.get(k): continue
        hits=likely_duplicate(i,rows)
        if hits: rep["sheet"]["possible_duplicates"]+=1; rep["possible_duplicate_items"].append({"yml_id":i["article"],"name":i["name"],"hits":hits}); continue
        new.append(i)
    if new:
        arr=[]
        for i in new:
            r=[""]*len(h)
            for c,v in {"Название":i["name"],"YML ID":i["article"],"Бренд":"Norden","Основное фото":imgf((i.get("images") or [""])[0]),"Фото":json.dumps(i.get("images") or [],ensure_ascii=False),
                        "Источник":"Norden","Тип Webasyst при загрузке":TYPE,"Закупка":ms(i["purchase"]),"РРЦ поставщика":ms(i["rrp"]),"Остаток":i["total"]}.items(): r[ix[c]]=v
            arr.append(r)
        start=len(rows)+2
        if ws.row_count<start+len(arr)+5: ws.resize(rows=start+len(arr)+5)
        for j in range(0,len(arr),50):
            b=arr[j:j+50]; ws.update(range_name=f"A{start+j}:{gspread.utils.rowcol_to_a1(start+j+len(b)-1,len(h))}",values=b,value_input_option="USER_ENTERED")
        rep["sheet"]["added"]=len(arr)
    h,ix,rows=read(ws); byone={nc(r.get("YML ID")):r for r in rows if s(r.get("YML ID"))}
    wa=WebasystClient(min_request_interval=0.45); tid,wstock,waby,wapid=wa_prepare(wa); wchanges=[]
    for k,i in src.items():
        r=byone.get(k)
        if not r: continue
        art=s(r.get("Артикул")); m=waby.get(nc(art),[]) if art else []
        try:
            if len(m)>1: rep["webasyst"]["ambiguous"]+=1; continue
            if len(m)==1: wa_update(wa,wstock,m[0][1],i); rep["webasyst"]["updated"]+=1
            elif art: rep["webasyst"]["errors"].append({"article":art,"yml_id":i["article"],"error":"Артикул есть в таблице, но точного SKU NORDEN-100 в Webasyst нет; не создавал дубль"})
            else:
                cr=wa_create(wa,tid,wstock,i); rep["webasyst"]["created"]+=1
                for c,v in {"Артикул":cr["article"],"Webasyst product_id":cr["pid"],"Webasyst sku_id":cr["sid"],"Тип Webasyst при загрузке":TYPE,"Webasyst URL":cr["url"],
                            "Цена Webasyst":ms(price(i["purchase"],"1.23")),"Старая цена Webasyst":ms(price(i["purchase"],"1.65")),"Закупочная цена Webasyst":ms(i["purchase"])}.items():
                    wchanges.append((r["_row"],c,v,False))
                waby[nc(cr["article"])].append(({"id":cr["pid"]},{"id":cr["sid"],"sku":cr["article"]}))
        except Exception as e: rep["webasyst"]["errors"].append({"article":art,"yml_id":i["article"],"error":str(e)[:800]})
    for r in rows:
        k=nc(r.get("YML ID")); art=s(r.get("Артикул"))
        if k and k not in src and art and actual_name(r.get("Название")):
            m=waby.get(nc(art),[])
            if len(m)==1:
                try: wa_update(wa,wstock,m[0][1],None); rep["webasyst"]["zeroed_missing"]+=1
                except Exception as e: rep["webasyst"]["errors"].append({"article":art,"error":str(e)[:800]})
    cells(ws,ix,wchanges)
    h,ix,rows=read(ws); byone={nc(r.get("YML ID")):r for r in rows if s(r.get("YML ID"))}
    kit=BRIDGE.KitClient(); msk_id,spb_id,kby,cats,chars=kit_prepare(kit); rev=[]
    for k,i in src.items():
        r=byone.get(k)
        if not r: continue
        art=s(r.get("Артикул"))
        if not art: continue
        m=kby.get(nc(art),[])
        try:
            if len(m)>1: rep["kit"]["ambiguous"]+=1; continue
            if len(m)==1: kit_stock_price(kit,m[0],i,msk_id,spb_id); rep["kit"]["updated"]+=1
            else:
                path=MOD.approved_category_path(i.get("category_path") or [])
                if not path: rev.append(i); rep["kit"]["category_review"]+=1; continue
                vid=kit_create(kit,art,i,path,msk_id,spb_id,cats,chars); kby[nc(art)].append({"id":vid,"sku":art}); rep["kit"]["created"]+=1
        except Exception as e: rep["kit"]["errors"].append({"article":art,"yml_id":i["article"],"error":str(e)[:800]})
    for r in rows:
        k=nc(r.get("YML ID")); art=s(r.get("Артикул"))
        if k and k not in src and art and actual_name(r.get("Название")):
            m=kby.get(nc(art),[])
            if len(m)==1:
                try: kit_stock_price(kit,m[0],None,msk_id,spb_id); rep["kit"]["zeroed_missing"]+=1
                except Exception as e: rep["kit"]["errors"].append({"article":art,"error":str(e)[:800]})
    if rev:
        review_sheet(sh,rev); rep["category_review_items"]=[{"yml_id":x["article"],"name":x["name"],"source_category":" > ".join(x.get("category_path") or [])} for x in rev[:300]]
    rep["complete"]=not rep["webasyst"]["errors"] and not rep["kit"]["errors"] and rep["sheet"]["ambiguous_yml"]==0
    rep["status"]="УСПЕШНО" if rep["complete"] else "ЗАВЕРШЕНО С ОШИБКАМИ"; rep["finished_at"]=now()
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps(rep,ensure_ascii=False,indent=2))
    return 0 if rep["complete"] else 2

if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as e:
        x={"started_at":now(),"finished_at":now(),"status":"ОШИБКА","complete":False,"fatal_error":str(e),"safety":"При неполном/ошибочном источнике массовое обнуление не выполняется."}
        REPORT.write_text(json.dumps(x,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps(x,ensure_ascii=False,indent=2)); raise
