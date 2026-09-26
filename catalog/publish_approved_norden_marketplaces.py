#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, json, math, os, re, sys, time
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
import gspread, requests
from google.oauth2.service_account import Credentials

ROOT=Path(__file__).resolve().parents[1]
SID=os.environ.get("CATALOG_SPREADSHEET_ID","1DvMYvKRr8fX8mfLFaeJCXCccQofPViPcIP2CgIctc8w")
SHEET=os.environ.get("CATALOG_SHEET","Норден")
BID=os.environ.get("YANDEX_MARKET_BUSINESS_ID","20806099")
CID=os.environ.get("YANDEX_MARKET_CAMPAIGN_ID","89405839")
OUT=ROOT/"catalog"/"norden_marketplace_publish_report.json"
TARGETS={x.strip() for x in os.environ.get("TARGET_ARTICLES","").split(",") if x.strip()}
OZON_BASE="https://api-seller.ozon.ru"
YANDEX_BASE="https://api.partner.market.yandex.ru"

def load(path,name):
    p=ROOT/path; spec=importlib.util.spec_from_file_location(name,p)
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
POST=load(Path("catalog")/"sync_ozon_readback_to_kit.py","postpublish")
BRIDGE=load(Path("norden-kit")/"webasyst_n100_to_kit_once.py","kit_bridge")
sys.path.insert(0,str(ROOT/"webasyst"))
from client import WebasystClient

def s(v): return str(v or "").strip()
def norm(v): return re.sub(r"\s+"," ",s(v)).casefold().strip()
def nfloat(v):
    try:return float(str(v).replace(",","."))
    except:return 0.0
def ceil(v): return int(Decimal(str(v)).to_integral_value(rounding=ROUND_CEILING))
def now(): return datetime.now(timezone.utc).isoformat()
def chunks(a,n):
    for i in range(0,len(a),n): yield a[i:i+n]

class API:
    def __init__(self):
        self.oz=requests.Session(); self.ya=requests.Session()
        self.oz.headers.update({"Client-Id":os.environ["OZON_CLIENT_ID"],"Api-Key":os.environ["OZON_API_KEY"],"Content-Type":"application/json","Accept":"application/json"})
        self.ya.headers.update({"Api-Key":os.environ["YANDEX_MARKET_API_KEY"],"Content-Type":"application/json","Accept":"application/json"})
    def req(self,which,method,path,body=None,params=None,retries=8):
        sess=self.oz if which=="oz" else self.ya
        base=OZON_BASE if which=="oz" else YANDEX_BASE
        last=None
        for attempt in range(retries):
            r=sess.request(method,base+path,json=body,params=params,timeout=120); last=r
            if r.status_code in (420,429) or r.status_code>=500:
                time.sleep(float(r.headers.get("Retry-After") or min(30,2**attempt))); continue
            try:data=r.json() if r.content else {}
            except:data={"raw":r.text}
            if r.status_code>=400: raise RuntimeError(f"{which} {path}: HTTP {r.status_code}: {json.dumps(data,ensure_ascii=False)[:2500]}")
            return data
        raise RuntimeError(f"{which} {path}: failed after retries, last={last.status_code if last else 'N/A'}")

api=API()
kit=BRIDGE.KitClient()
wa=WebasystClient(min_request_interval=0.45)

# Sheet
creds=json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
gc=gspread.authorize(Credentials.from_service_account_info(creds,scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]))
ws=gc.open_by_key(SID).worksheet(SHEET)
vals=ws.get_all_values(); headers=vals[0]; ix={h:i for i,h in enumerate(headers)}
required_cols=["Артикул","YML ID","Webasyst product_id","KIT variant_id","KIT ID","Проверено — загрузить в Ozon и Яндекс.Маркет",
"Ozon product_id","Ozon offer_id","Ozon category_id","Ozon type_id","Ozon sale_schema","Ozon комиссия %","Ozon эквайринг %","Ozon статус","Ozon дата",
"Ozon Предельная цена без акций","Ozon Зачёркнутая цена","Ozon Ограничение для акций и стратегий","Ozon media JSON",
"Yandex offer_id","Yandex business_id","Yandex campaign_id","Yandex category_id","Yandex модель размещения","Yandex tariffs JSON","Yandex комиссия %","Yandex эквайринг %","Yandex статус","Yandex дата","Yandex цена","Yandex зачёркнутая цена","Yandex media JSON","Закупочная цена Webasyst"]
miss=[x for x in required_cols if x not in ix]
if miss: raise RuntimeError("Missing sheet columns: "+", ".join(miss))

def cell(row,col):
    return s(row[ix[col]] if ix[col]<len(row) else "")
def truth(v): return s(v).casefold() in ("true","1","да","yes")
def update_row(rn,pairs):
    req=[{"range":gspread.utils.rowcol_to_a1(rn,ix[k]+1),"values":[[v]]} for k,v in pairs.items()]
    if req: ws.batch_update(req,value_input_option="RAW")

selected=[]
for rn,row in enumerate(vals[1:],2):
    art=cell(row,"Артикул")
    if not art or (TARGETS and art not in TARGETS): continue
    if not truth(cell(row,"Проверено — загрузить в Ozon и Яндекс.Маркет")): continue
    if not cell(row,"KIT variant_id") or not cell(row,"KIT ID") or not cell(row,"Webasyst product_id"): continue
    selected.append({"rn":rn,"row":row,"article":art})

# KIT helpers
char_meta={s(x.get("id")):s(x.get("title")) for x in kit.characteristics()}
def exact_variant(art,expected):
    v=kit.request("GET",f"/v1/variants/{expected}")
    if s(v.get("sku"))!=art: raise RuntimeError(f"KIT SKU mismatch {art}: {v.get('sku')}")
    return v
def kit_prep(v):
    out={}
    for c in v.get("characteristics") or []:
        title=char_meta.get(s(c.get("characteristic_id")),"")
        if title.startswith("Ozon "):
            out[title]=s(c.get("value"))
    return out
def ozval(prep,aid,name=None):
    prefix=f"Ozon {aid} — "
    for k,v in prep.items():
        if k.startswith(prefix) and (name is None or k==prefix+name): return v
    return ""
def ozid(prep,aid):
    x=ozval(prep,aid)
    return x

# Webasyst numeric KIT ID
def set_wa_kit_id(pid,kid):
    before=wa.call("shop.product.getInfo",params={"id":pid})
    bf=before.get("features") or {}
    current=s(bf.get("kit_id") if isinstance(bf,dict) else "")
    if current!=str(kid):
        wa.call("shop.product.update",http_method="POST",params={"id":pid},data={"features":{"kit_id":str(kid)}})
    after=wa.call("shop.product.getInfo",params={"id":pid})
    af=after.get("features") or {}
    got=s(af.get("kit_id") if isinstance(af,dict) else "")
    if got!=str(kid): raise RuntimeError(f"Webasyst KIT ID mismatch {got} != {kid}")
    return got

# Ozon
_schema_cache={}; _dict_cache={}
def oz_schema(dc,tid):
    key=(int(dc),int(tid))
    if key not in _schema_cache:
        d=api.req("oz","POST","/v1/description-category/attribute",{"description_category_id":key[0],"type_id":key[1],"language":"DEFAULT"})
        _schema_cache[key]={int(x.get("id") or 0):x for x in (d.get("result") or []) if int(x.get("id") or 0)}
    return _schema_cache[key]
def oz_dict(dc,tid,aid):
    key=(int(dc),int(tid),int(aid))
    if key in _dict_cache:return _dict_cache[key]
    vals=[]; last=0; seen=set()
    for _ in range(12):
        d=api.req("oz","POST","/v1/description-category/attribute/values",{"description_category_id":key[0],"type_id":key[1],"attribute_id":key[2],"language":"DEFAULT","last_value_id":last,"limit":5000})
        batch=d.get("result") or []
        if not batch:break
        vals.extend(batch)
        nxt=int(batch[-1].get("id") or 0)
        if not nxt or nxt==last or nxt in seen or len(batch)<5000:break
        seen.add(nxt);last=nxt
    _dict_cache[key]=vals;return vals
def dict_value(dc,tid,aid,text):
    if aid==85 and norm(text)=="norden": return 970787045,"Norden"
    if aid==8229 and "офис" in norm(text) and "крес" in norm(text): return 95041,"Офисное кресло"
    vals=oz_dict(dc,tid,aid); nt=norm(text)
    exact=[x for x in vals if norm(x.get("value"))==nt]
    if exact:return int(exact[0]["id"]),s(exact[0].get("value"))
    if aid==22232:
        pref=[x for x in vals if s(x.get("value")).startswith(s(text).split()[0])]
        if pref:return int(pref[0]["id"]),s(pref[0].get("value"))
    return 0,text
def oz_existing(offer):
    d=api.req("oz","POST","/v3/product/list",{"filter":{"offer_id":[offer],"visibility":"ALL"},"limit":100})
    items=((d.get("result") or {}).get("items") or [])
    return [x for x in items if s(x.get("offer_id"))==offer]
def oz_import_attributes(prep,dc,tid):
    schema=oz_schema(dc,tid); attrs=[]; warnings=[]
    ids=sorted({int(m.group(1)) for k in prep for m in [re.match(r"^Ozon (\d+) — ",k)] if m and int(m.group(1))>0})
    for aid in ids:
        meta=schema.get(aid)
        if not meta: continue
        # skip internal dictionary id mirror characteristics
        valsrc=ozval(prep,aid)
        if not valsrc: continue
        pieces=[x.strip() for x in valsrc.split(";") if x.strip()]
        values=[]
        if int(meta.get("dictionary_id") or 0):
            for p in pieces:
                did,dtext=dict_value(dc,tid,aid,p)
                if did: values.append({"dictionary_value_id":did,"value":dtext})
                else: warnings.append(f"{aid} {meta.get('name')}: dictionary value not resolved: {p}")
        else:
            values=[{"value":p} for p in pieces]
        if values: attrs.append({"complex_id":0,"id":aid,"values":values})
    have={x["id"] for x in attrs}
    missing=[(aid,m.get("name")) for aid,m in schema.items() if m.get("is_required") and aid not in have]
    if missing: raise RuntimeError("Ozon required attributes missing: "+str(missing))
    return attrs,warnings
def oz_import_item(art,prep,v,purchase):
    dc=int(ozval(prep,0,"description_category_id") or 0); tid=int(ozval(prep,0,"type_id") or 0)
    if not dc or not tid: raise RuntimeError("KIT Ozon category/type missing")
    attrs,warnings=oz_import_attributes(prep,dc,tid)
    boxes=v.get("cargo_boxes") or []; box=boxes[0] if boxes else {}
    L=int(box.get("length") or 0);W=int(box.get("width") or 0);H=int(box.get("height") or 0);weight=max(31000,int(box.get("weight") or 0))
    if min(L,W,H)<=0: raise RuntimeError("KIT cargo dimensions missing")
    media=POST.BRIDGE.KitClient() if False else None
    urls=[]
    for m in sorted([x for x in (v.get("media") or []) if s(x.get("type")).upper()=="IMAGE"],key=lambda x:int(x.get("display_sequence") or 0)):
        fid=s(m.get("image_id"))
        if fid:
            fm=kit.request("GET",f"/v1/files/{fid}"); u=s(fm.get("url"))
            if u:urls.append(u)
    if not urls:raise RuntimeError("KIT public images missing")
    # Initial Ozon office-chair rate; exact rate is read back and price corrected immediately after creation.
    commission=51.0; acquiring=2.0
    price=ceil(purchase*1.21/(1-commission/100-acquiring/100));old=ceil(price*1.6)
    item={"attributes":attrs,"barcode":"","description_category_id":dc,"new_description_category_id":0,"type_id":tid,
          "color_image":"","complex_attributes":[],"currency_code":"RUB","depth":L*10,"width":W*10,"height":H*10,"dimension_unit":"mm",
          "weight":weight,"weight_unit":"g","images":urls,"images360":[],"name":ozval(prep,4180) or s(v.get("name")),
          "offer_id":art,"old_price":str(old),"price":str(price),"primary_image":urls[0],"vat":"0"}
    return item,urls,warnings
def oz_create(art,item):
    d=api.req("oz","POST","/v3/product/import",{"items":[item]})
    task=int((d.get("result") or {}).get("task_id") or d.get("task_id") or 0)
    if not task:raise RuntimeError("Ozon import returned no task_id: "+json.dumps(d,ensure_ascii=False)[:1800])
    last={}
    for _ in range(30):
        time.sleep(4)
        last=api.req("oz","POST","/v1/product/import/info",{"task_id":task})
        items=((last.get("result") or {}).get("items") or [])
        exact=[x for x in items if s(x.get("offer_id"))==art]
        if exact:
            x=exact[0]; errs=x.get("errors") or []
            severe=[e for e in errs if s(e.get("level")).upper() in ("ERROR","FATAL") or s(e.get("state")).upper() in ("ERROR","FAILED")]
            status=s(x.get("status")).casefold()
            if severe or status in ("failed","error","not_created"):
                raise RuntimeError("Ozon import failed: "+json.dumps(x,ensure_ascii=False)[:3500])
            if int(x.get("product_id") or 0)>0 or status in ("imported","moderating","created","processed"):
                break
    for _ in range(20):
        ex=oz_existing(art)
        if len(ex)==1:return int(ex[0].get("product_id") or 0),task
        time.sleep(4)
    raise RuntimeError("Ozon product not visible by exact offer_id after import")
def oz_commission_and_price(art,purchase,pid):
    commission=51.0
    for _ in range(10):
        d=api.req("oz","POST","/v5/product/info/prices",{"cursor":"","filter":{"offer_id":[art],"visibility":"ALL"},"limit":100})
        ex=[x for x in (d.get("items") or []) if s(x.get("offer_id"))==art]
        if ex:
            commission=nfloat((ex[0].get("commissions") or {}).get("sales_percent_rfbs")) or commission
            break
        time.sleep(3)
    acquiring=2.0
    price=ceil(purchase*1.21/(1-commission/100-acquiring/100));old=ceil(price*1.6);minp=ceil(purchase*1.18/(1-commission/100-acquiring/100))
    d=api.req("oz","POST","/v1/product/import/prices",{"prices":[{"product_id":pid,"offer_id":art,"price":str(price),"old_price":str(old),"min_price":str(minp),"currency_code":"RUB","min_price_for_auto_actions_enabled":True,"price_strategy_enabled":"DISABLED"}]})
    rr=(d.get("result") or [{}])[0]
    if not rr.get("updated") or rr.get("errors"):raise RuntimeError("Ozon price update failed: "+json.dumps(rr,ensure_ascii=False))
    return commission,acquiring,price,old,minp

# Yandex
_ycat={}
def ycat_schema(cid):
    if cid not in _ycat:
        d=api.req("ya","POST",f"/v2/category/{cid}/parameters",{"language":"RU"})
        _ycat[cid]=((d.get("result") or {}).get("parameters") or [])
    return _ycat[cid]
def yandex_category(v):
    prod=kit.request("GET",f"/v1/products/{v.get('product_id')}")
    ids=prod.get("category_ids") or []
    cats={s(x.get("id")):x for x in kit.categories()}
    names=[]
    for cid in ids:
        cur=s(cid);path=[];seen=set()
        while cur and cur not in seen and cur in cats:
            seen.add(cur);x=cats[cur];path.append(s(x.get("title") or x.get("name")));cur=s(x.get("parent_id"))
        names.append(" > ".join(reversed([x for x in path if x])))
    joined=" | ".join(names).casefold()
    if "офисные кресла" in joined or "кресла офисные" in joined:return 10785222,"Компьютерные кресла"
    if "столы и стулья > стулья" in joined:return 61276996,"Стулья"
    raise RuntimeError("No approved Yandex category mapping for KIT path: "+joined)
def yandex_tariff(category,purchase,box):
    provisional=max(1000,ceil(purchase*3))
    body={"parameters":{"campaignId":int(CID)},"offers":[{"categoryId":int(category),"price":provisional,"length":int(box["length"]),"width":int(box["width"]),"height":int(box["height"]),"weight":max(31.0,nfloat(box.get("weight"))/1000),"quantity":1}]}
    d=api.req("ya","POST","/v2/tariffs/calculate",body)
    offers=((d.get("result") or {}).get("offers") or [])
    if len(offers)!=1:raise RuntimeError("Yandex tariff response invalid")
    tariffs=offers[0].get("tariffs") or []
    fee=0.0;pay=0.0
    for t in tariffs:
        typ=s(t.get("type"))
        params={s(x.get("name")):s(x.get("value")) for x in (t.get("parameters") or [])}
        if typ=="FEE": fee=nfloat(params.get("value")) or nfloat(t.get("amount"))/provisional*100
        if typ in ("AGENCY_COMMISSION","PAYMENT_TRANSFER"):
            if s(params.get("valueType")).casefold()=="relative":pay+=nfloat(params.get("value"))
            else:pay+=nfloat(t.get("amount"))/provisional*100
    if fee<=0:raise RuntimeError("Yandex FEE tariff not resolved")
    sale=ceil(purchase*1.21/(1-fee/100-pay/100));old=ceil(sale*1.6)
    return tariffs,fee,pay,sale,old
def yparam_values(category,prep):
    ps=ycat_schema(category); out=[]
    # source values by Yandex parameter name
    src={
      "Материал обивки":ozval(prep,21909),"Материал каркаса":ozval(prep,6656),
      "Название цвета от производителя":ozval(prep,10097),"Назначение":"офисное",
      "Регулировка":ozval(prep,11256),"Максимальная нагрузка":ozval(prep,7915),
      "Высота спинки":ozval(prep,6667),"Высота сиденья":ozval(prep,6664),
      "Глубина сиденья":ozval(prep,6666),"Ширина сиденья":ozval(prep,6665),
      "Цвет для фильтра":ozval(prep,10096),"Тип механизма качания":ozval(prep,10036),
      "Материал наполнителя":ozval(prep,6643),"Высота кресла":ozval(prep,10174),
      "Максимальная высота кресла":ozval(prep,10906),"Максимальная высота сиденья":ozval(prep,11280),
      "Вес":str(nfloat(ozval(prep,4383))/1000) if nfloat(ozval(prep,4383)) else "",
      "Материал крестовины":ozval(prep,10035),
      "Ширина":ozval(prep,10175),"Глубина":ozval(prep,10176),"Высота":ozval(prep,10174),
    }
    for p in ps:
        name=s(p.get("name")); raw=s(src.get(name))
        if not raw:continue
        vals=[x.strip() for x in raw.split(";") if x.strip()]
        if p.get("type")=="ENUM":
            allowed=p.get("values") or []
            if name=="Материал обивки" and len(vals)>1:
                both=[x for x in allowed if all(k in norm(x.get("value")) for k in ("сет","текст"))]
                if both: vals=[s(both[0].get("value"))]
            for val in vals:
                exact=[x for x in allowed if norm(x.get("value"))==norm(val)]
                if exact: out.append({"parameterId":int(p["id"]),"valueId":int(exact[0]["id"])})
                elif p.get("allowCustomValues"): out.append({"parameterId":int(p["id"]),"value":val})
        else:
            obj={"parameterId":int(p["id"]),"value":raw}
            if p.get("unit") and p["unit"].get("defaultUnitId"):obj["unitId"]=int(p["unit"]["defaultUnitId"])
            out.append(obj)
    return out
def ya_mapping(art):
    d=api.req("ya","POST",f"/v2/businesses/{BID}/offer-mappings",{"offerIds":[art]})
    rows=((d.get("result") or {}).get("offerMappings") or [])
    return [x for x in rows if s((x.get("offer") or {}).get("offerId"))==art]
def ya_create(art,offer):
    d=api.req("ya","POST",f"/v2/businesses/{BID}/offer-mappings/update",{"offerMappings":[{"offer":offer}]})
    results=d.get("results") or []
    exact=[x for x in results if s(x.get("offerId"))==art]
    if exact and exact[0].get("errors"):raise RuntimeError("Yandex content create errors: "+json.dumps(exact[0],ensure_ascii=False)[:3000])
    # place in DBS store; this touches only the just-created SKU.
    api.req("ya","POST",f"/v2/campaigns/{CID}/offers/update",{"offers":[{"offerId":art,"available":True}]})
    for _ in range(10):
        ex=ya_mapping(art)
        if len(ex)==1:return ex[0]
        time.sleep(5)
    raise RuntimeError("Yandex exact offer not visible after create")
def ya_price(art,sale,old):
    d=api.req("ya","POST",f"/v2/businesses/{BID}/offer-prices/updates",{"offers":[{"offerId":art,"price":{"value":int(sale),"currencyId":"RUR","discountBase":int(old)}}]})
    if s(d.get("status")).upper() not in ("","OK") or d.get("errors"):raise RuntimeError("Yandex price update failed: "+json.dumps(d,ensure_ascii=False))
def ya_stock(art,count):
    # This campaign currently uses the campaign stock method in the existing DBS integration.
    d=api.req("ya","PUT",f"/v2/campaigns/{CID}/offers/stocks",{"skus":[{"sku":art,"items":[{"count":max(0,int(count))}]}]})
    if s(d.get("status")).upper() not in ("","OK") or d.get("errors"):raise RuntimeError("Yandex stock update failed: "+json.dumps(d,ensure_ascii=False))
def yandex_offer(art,yml,prep,v,category,catname,sale,old):
    boxes=v.get("cargo_boxes") or [];box=boxes[0]; urls=[]
    for m in sorted([x for x in (v.get("media") or []) if s(x.get("type")).upper()=="IMAGE"],key=lambda x:int(x.get("display_sequence") or 0)):
        fm=kit.request("GET",f"/v1/files/{s(m.get('image_id'))}");u=s(fm.get("url"))
        if u:urls.append(u)
    tn=s(ozval(prep,22232)).split()[0]
    country=ozval(prep,4389)
    offer={"offerId":art,"name":ozval(prep,4180) or s(v.get("name")),"marketCategoryId":int(category),"pictures":urls[:30],
           "vendor":"Norden","vendorCode":yml,"description":ozval(prep,4191),
           "weightDimensions":{"length":int(box["length"]),"width":int(box["width"]),"height":int(box["height"]),"weight":max(31.0,nfloat(box.get("weight"))/1000)},
           "boxCount":1,"parameterValues":yparam_values(category,prep),
           "basicPrice":{"value":int(sale),"currencyId":"RUR","discountBase":int(old)}}
    if country:offer["manufacturerCountries"]=[country]
    if tn:offer["commodityCodes"]=[{"code":tn,"type":"CUSTOMS_COMMODITY_CODE"}]
    return offer,urls

report={"started_at":now(),"status":"УСПЕШНО","selected":len(selected),"items":[]}
for r in selected:
    art=r["article"];row=r["row"];rn=r["rn"]
    item={"article":art,"status":"УСПЕШНО","ozon":{},"yandex":{}}
    try:
        vid=cell(row,"KIT variant_id");kid=cell(row,"KIT ID");pid=cell(row,"Webasyst product_id");yml=cell(row,"YML ID")
        v=exact_variant(art,vid); prep=kit_prep(v)
        # Mandatory preparation marker
        if ozval(prep,0,"Статус подготовки")!="ПОДГОТОВЛЕНО В KIT — НЕ ВЫГРУЖЕНО" and ozval(prep,0,"Статус данных")!="КАНОНИЧЕСКИЕ ДАННЫЕ ИЗ OZON":
            raise RuntimeError("KIT Ozon preparation status missing")
        set_wa_kit_id(pid,kid)
        purchase=nfloat(cell(row,"Закупочная цена Webasyst"))
        if purchase<=0:raise RuntimeError("Purchase price missing")

        # OZON old-card protection / creation
        existing=oz_existing(art)
        prior_status=cell(row,"Ozon статус"); prior_pid=cell(row,"Ozon product_id")
        if existing:
            ep=str(existing[0].get("product_id") or "")
            if not (prior_status.startswith("CREATE_STARTED") or prior_status.startswith("CREATED_BY_PIPELINE") or (prior_pid and prior_pid==ep)):
                item["ozon"]={"status":"ПРОПУЩЕНО_СТАРАЯ_КАРТОЧКА","product_id":ep}
            else:
                post=POST.sync_offer_to_kit(art,vid)
                item["ozon"]={"status":"CREATED_BY_PIPELINE_EXISTING","product_id":ep,"postpublish":post}
        else:
            update_row(rn,{"Ozon статус":"CREATE_STARTED:"+now(),"Ozon offer_id":art,"Ozon sale_schema":"RFBS"})
            ozitem,ozurls,warns=oz_import_item(art,prep,v,purchase)
            opid,task=oz_create(art,ozitem)
            # Mandatory automatic post-publish canonical replacement in KIT.
            post=POST.sync_offer_to_kit(art,vid)
            commission,acq,oprice,oold,omin=oz_commission_and_price(art,purchase,opid)
            rb=POST.ozon_readback(POST.OzonClient(),art)
            oattrs=rb["attributes"]
            update_row(rn,{"Ozon product_id":opid,"Ozon offer_id":art,"Ozon sale_schema":"RFBS","Ozon комиссия %":commission,"Ozon эквайринг %":acq,
                "Ozon Предельная цена без акций":oprice,"Ozon Зачёркнутая цена":oold,"Ozon Ограничение для акций и стратегий":omin,
                "Ozon media JSON":json.dumps(oattrs.get("images") or [],ensure_ascii=False),"Ozon статус":"CREATED_BY_PIPELINE:"+s((rb["info"].get("statuses") or {}).get("status_name")),"Ozon дата":now()})
            item["ozon"]={"status":"CREATED_BY_PIPELINE","product_id":opid,"task_id":task,"commission":commission,"acquiring":acq,"price":oprice,"old_price":oold,"min_price":omin,"postpublish":post,"warnings":warns}

        # Refresh KIT because Ozon postpublish replaced temp Ozon block with canonical Ozon block.
        v=exact_variant(art,vid); prep=kit_prep(v)
        # Yandex old-card protection / creation
        existing_y=ya_mapping(art); prior_ys=cell(row,"Yandex статус")
        if existing_y and not (prior_ys.startswith("CREATE_STARTED") or prior_ys.startswith("CREATED_BY_PIPELINE")):
            item["yandex"]={"status":"ПРОПУЩЕНО_СТАРАЯ_КАРТОЧКА"}
        elif existing_y:
            item["yandex"]={"status":"CREATED_BY_PIPELINE_EXISTING"}
        else:
            update_row(rn,{"Yandex статус":"CREATE_STARTED:"+now(),"Yandex offer_id":art,"Yandex business_id":BID,"Yandex campaign_id":CID,"Yandex модель размещения":"DBS"})
            cat,catname=yandex_category(v); box=(v.get("cargo_boxes") or [None])[0]
            if not box:raise RuntimeError("KIT cargo box missing for Yandex")
            tariffs,fee,pay,ysale,yold=yandex_tariff(cat,purchase,box)
            yoffer,yurls=yandex_offer(art,yml,prep,v,cat,catname,ysale,yold)
            yrb=ya_create(art,yoffer)
            ya_price(art,ysale,yold)
            # Current prompt: stock mirrors factual Norden/KIT Moscow stock. Take first KIT warehouse quantity conservatively max.
            stock=max([int(x.get("quantity") or 0) for x in (v.get("stocks") or [])] or [0])
            try: ya_stock(art,stock); stock_status="UPDATED"
            except Exception as se: stock_status="WARNING:"+str(se)[:400]
            update_row(rn,{"Yandex offer_id":art,"Yandex business_id":BID,"Yandex campaign_id":CID,"Yandex category_id":cat,"Yandex категория":catname,
                "Yandex модель размещения":"DBS","Yandex tariffs JSON":json.dumps(tariffs,ensure_ascii=False,separators=(",",":")),
                "Yandex комиссия %":fee,"Yandex эквайринг %":pay,"Yandex цена":ysale,"Yandex зачёркнутая цена":yold,
                "Yandex media JSON":json.dumps(yurls,ensure_ascii=False),"Yandex статус":"CREATED_BY_PIPELINE","Yandex дата":now()})
            item["yandex"]={"status":"CREATED_BY_PIPELINE","category_id":cat,"category":catname,"commission":fee,"acquiring":pay,"price":ysale,"old_price":yold,"stock":stock_status}
    except Exception as e:
        item["status"]="ЗАВЕРШЕНО С ОШИБКАМИ";item["error"]=str(e)[:5000];report["status"]="ЗАВЕРШЕНО С ОШИБКАМИ"
    report["items"].append(item)

report["finished_at"]=now()
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
print(json.dumps(report,ensure_ascii=False,default=str))
raise SystemExit(0 if report["status"]=="УСПЕШНО" else 2)
