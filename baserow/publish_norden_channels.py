#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, json, os, sys, time, unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import requests

HERE=Path(__file__).resolve().parent; ROOT=HERE.parent
sys.path.insert(0,str(ROOT/"webasyst"))
from client import WebasystClient

BASEROW_URL=os.environ.get("BASEROW_URL","http://147.78.67.6").rstrip("/")
TOKEN=os.environ.get("BASEROW_DATABASE_TOKEN","").strip()
WEBASYST_BASE=os.environ.get("WEBASYST_BASE_URL","https://profikompany.ru").rstrip("/")
WEBASYST_TOKEN=os.environ.get("WEBASYST_API_TOKEN","").strip()
KIT_TOKEN=os.environ.get("YANDEX_KIT_TOKEN","").strip()
OZON_CLIENT=os.environ.get("OZON_CLIENT_ID","").strip(); OZON_KEY=os.environ.get("OZON_API_KEY","").strip()
YA_KEY=os.environ.get("YANDEX_MARKET_API_KEY","").strip()
YA_BUSINESS=str(os.environ.get("YANDEX_MARKET_BUSINESS_ID") or "20806099")
YA_CAMPAIGN=str(os.environ.get("YANDEX_MARKET_CAMPAIGN_ID") or "89405839")
OZON_WAREHOUSE=int(os.environ.get("OZON_NORDEN_MSK_WAREHOUSE_ID") or "1020005029910180")
YA_WAREHOUSE=int(os.environ.get("YANDEX_NORDEN_MSK_WAREHOUSE_ID") or "1274413")
CATALOG=156; SUPPLIERS=157; SUPPLIER="Norden"; WA_TYPE="NORDEN-100"
FIELD_STOCK="Norden MSK"; FIELD_KIT="Артикул KIT"
REPORT=HERE/"norden_channel_publish_report.json"
LIVE_INDEX=HERE/"kit_live_index.json"

def s(v): return str(v or "").strip()
def norm(v): return unicodedata.normalize("NFKC",s(v)).casefold()
def n(v):
    try:return float(str(v).replace("\xa0","").replace(" ","").replace(",","."))
    except:return 0.0
def qty(v):
    try:return max(0,int(float(str(v or 0).replace(",","."))))
    except:return 0
def mstr(v): return f"{n(v):.2f}"
def now(): return datetime.now(timezone.utc).isoformat()
def chunks(a,k):
    for i in range(0,len(a),k): yield a[i:i+k]
def listify(p,keys=()):
    if isinstance(p,list): return [x for x in p if isinstance(x,dict)]
    if not isinstance(p,dict): return []
    for k in keys:
        v=p.get(k)
        if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
        if isinstance(v,dict): return [x for x in v.values() if isinstance(x,dict)]
    return []

class BR:
    def __init__(self):
        self.ses=requests.Session(); self.ses.headers.update({"Authorization":"Token "+TOKEN,"Accept":"application/json"})
    def rows(self,tid):
        out=[]; page=1
        while True:
            r=self.ses.get(BASEROW_URL+f"/api/database/rows/table/{tid}/?user_field_names=true&size=200&page={page}",timeout=90)
            if not r.ok: raise RuntimeError(f"Baserow HTTP {r.status_code}: {r.text[:1000]}")
            d=r.json(); out.extend(d.get("results") or [])
            if not d.get("next"): return out
            page+=1

def supplier_ids(row):
    out=set()
    for x in row.get("Поставщик") or []:
        if isinstance(x,dict) and x.get("id") is not None:
            try:out.add(int(x["id"]))
            except:pass
    return out

def product_skus(p):
    v=p.get("skus")
    if isinstance(v,dict): return [x for x in v.values() if isinstance(x,dict)]
    if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
    return []

def load_kit():
    p=ROOT/"norden-kit"/"sync_norden_kit.py"
    sp=importlib.util.spec_from_file_location("kitmod",p); mod=importlib.util.module_from_spec(sp); sp.loader.exec_module(mod); return mod

def api(session,method,url,body=None,retries=8):
    last=None
    for i in range(retries):
        try:r=session.request(method,url,json=body,timeout=120)
        except requests.RequestException as e:
            last=e; time.sleep(min(20,2**i)); continue
        if r.status_code in (420,429) or r.status_code>=500:
            last=RuntimeError(f"HTTP {r.status_code}: {r.text[:700]}"); time.sleep(float(r.headers.get("Retry-After") or min(30,2**i))); continue
        if not r.ok: raise RuntimeError(f"{method} {url} HTTP {r.status_code}: {r.text[:1500]}")
        return r.json() if r.content else {}
    raise RuntimeError(str(last))

def main():
    if not all((TOKEN,WEBASYST_TOKEN,KIT_TOKEN,OZON_CLIENT,OZON_KEY,YA_KEY)): raise RuntimeError("Required secrets are missing")
    br=BR(); suppliers=br.rows(SUPPLIERS)
    sm=[r for r in suppliers if norm(r.get("Поставщик"))==norm(SUPPLIER)]
    if len(sm)!=1: raise RuntimeError(f"Expected one Norden supplier, got {len(sm)}")
    sid=int(sm[0]["id"])
    rows=[r for r in br.rows(CATALOG) if sid in supplier_ids(r) or norm(r.get("Бренд"))=="norden"]
    by_article={s(r.get("Артикул")):r for r in rows if s(r.get("Артикул"))}
    report={"started_at":now(),"rows":len(rows),"webasyst":{},"kit":{},"ozon":{},"yandex":{}}

    # Webasyst: all existing Norden-100 products, regardless of marketplace checkboxes.
    wa=WebasystClient(base_url=WEBASYST_BASE,token=WEBASYST_TOKEN,min_request_interval=0.35)
    types=listify(wa.call("shop.type.getList"))
    tm=[x for x in types if norm(x.get("name") or x.get("title"))==norm(WA_TYPE)]
    if len(tm)!=1: raise RuntimeError(f"Webasyst type {WA_TYPE} count={len(tm)}")
    type_id=s(tm[0].get("id"))
    stocks=listify(wa.call("shop.stock.getList"))
    stock_ids={s(x.get("name") or x.get("title")):s(x.get("id")) for x in stocks if s(x.get("id"))}
    main_ids=[v for k,v in stock_ids.items() if norm(k)==norm("Основной склад")]
    if len(main_ids)!=1: raise RuntimeError("Webasyst Основной склад not uniquely found")
    main_id=main_ids[0]; all_ids=list(dict.fromkeys(stock_ids.values()))
    products=[]; off=0
    while True:
        d=wa.call("shop.product.search",params={"hash":f"type/{type_id}","offset":off,"limit":1000,"fields":"*,skus,stock_counts"})
        b=listify(d,("products","items")); products.extend(b)
        if not b or len(b)<1000: break
        off+=len(b)
    wa_updated=wa_matched=0; wa_errors=[]
    for p in products:
        for sk in product_skus(p):
            art=s(sk.get("sku")); r=by_article.get(art)
            if not r: continue
            wa_matched+=1
            sale=n(r.get("Цена продажи Webasyst")); old=n(r.get("Цена зачеркнутая Webasyst")); purchase=n(r.get("Закупка Norden"))
            data={"stock":{x:str(qty(r.get(FIELD_STOCK)) if x==main_id else 0) for x in all_ids}}
            if sale>0:data["price"]=mstr(sale)
            if old>0:data["compare_price"]=mstr(old)
            if purchase>0:data["purchase_price"]=mstr(purchase)
            try:
                wa.call("shop.product.skus.update",http_method="POST",params={"id":s(sk.get("id"))},data=data); wa_updated+=1
            except Exception as e: wa_errors.append({"article":art,"error":str(e)[:700]})
    report["webasyst"]={"matched":wa_matched,"updated":wa_updated,"errors":wa_errors[:100]}

    # KIT: all mapped rows, stocks to MСК + СПБ привозной and calculated prices.
    mod=load_kit(); kit=mod.KitClient(KIT_TOKEN); wh=mod.resolve_warehouses(kit)
    target=[wh["МСК"],wh["СПБ привозной"]]
    live=json.loads(LIVE_INDEX.read_text(encoding="utf-8")) if LIVE_INDEX.exists() else {}
    stock_items=[]; price_items=[]; kit_rows=0
    for r in rows:
        try:kid=str(int(float(r.get(FIELD_KIT))))
        except:continue
        cand=live.get(kid) or []
        vids=list(dict.fromkeys(s(x.get("variant_id")) for x in cand if s(x.get("variant_id"))))
        if len(vids)!=1: continue
        vid=vids[0]; kit_rows+=1; q=qty(r.get(FIELD_STOCK))
        for wid in target: stock_items.append({"variant_id":vid,"warehouse_id":wid,"quantity":q})
        old=n(r.get("Цена KIT до скидки")); sale=n(r.get("Цена KIT со скидкой"))
        if old>0 and sale>0: price_items.append({"variant_id":vid,"price":mstr(old),"manual_discount_price":mstr(sale)})
    skipped=kit.bulk_stocks(stock_items) if stock_items else []
    kit_price_errors=[]
    for batch in chunks(price_items,500):
        try: kit.request("POST","/v1/variants/prices/bulk_update",body={"items":batch})
        except Exception as e: kit_price_errors.append(str(e)[:1000])
    report["kit"]={"mapped_rows":kit_rows,"stock_pairs":len(stock_items),"stock_skipped":len(skipped),"price_rows":len(price_items),"price_errors":kit_price_errors}

    # Ozon existing products: all prices + Moscow stock, flags do not limit updates.
    oz=requests.Session(); oz.headers.update({"Client-Id":OZON_CLIENT,"Api-Key":OZON_KEY,"Content-Type":"application/json","Accept":"application/json"})
    oz_existing={}
    arts=list(by_article)
    for batch in chunks(arts,100):
        d=api(oz,"POST","https://api-seller.ozon.ru/v5/product/info/prices",{"cursor":"","filter":{"offer_id":batch,"visibility":"ALL"},"limit":100})
        for x in d.get("items") or []:
            a=s(x.get("offer_id"))
            if a:oz_existing[a]=x
    oz_prices=[]; oz_stocks=[]
    for art,x in oz_existing.items():
        r=by_article.get(art)
        if not r: continue
        sale=qty(r.get("Ozon Предельная цена без акций")); old=qty(r.get("Ozon Зачёркнутая цена")); mn=qty(r.get("Ограничение для акций и стратегий"))
        pid=int(x.get("product_id") or 0)
        if sale>0 and old>sale and 0<mn<=sale:
            item={"offer_id":art,"price":str(sale),"old_price":str(old),"min_price":str(mn),"currency_code":"RUB","min_price_for_auto_actions_enabled":True,"price_strategy_enabled":"DISABLED"}
            if pid:item["product_id"]=pid
            oz_prices.append(item)
        oz_stocks.append({"offer_id":art,"stock":qty(r.get(FIELD_STOCK)),"warehouse_id":OZON_WAREHOUSE})
    oz_price_ok=0; oz_price_errors=[]
    for batch in chunks(oz_prices,100):
        d=api(oz,"POST","https://api-seller.ozon.ru/v1/product/import/prices",{"prices":batch})
        rr=d.get("result") or []
        oz_price_ok+=sum(1 for x in rr if x.get("updated") and not x.get("errors"))
        oz_price_errors.extend([x for x in rr if not x.get("updated") or x.get("errors")][:100])
    oz_stock_ok=0; oz_stock_errors=[]
    for batch in chunks(oz_stocks,100):
        d=api(oz,"POST","https://api-seller.ozon.ru/v2/products/stocks",{"stocks":batch})
        rr=d.get("result") or []
        for x in rr:
            if x.get("updated") and not x.get("errors"): oz_stock_ok+=1
            else: oz_stock_errors.append(x)
    report["ozon"]={"existing_offers":len(oz_existing),"prices_targeted":len(oz_prices),"prices_updated":oz_price_ok,"price_errors":oz_price_errors[:100],"stocks_targeted":len(oz_stocks),"stocks_updated":oz_stock_ok,"stock_errors":oz_stock_errors[:100],"warehouse_id":OZON_WAREHOUSE}

    # Yandex existing products: all prices + explicit partner warehouse stock.
    ya=requests.Session(); ya.headers.update({"Api-Key":YA_KEY,"Content-Type":"application/json","Accept":"application/json"})
    ya_existing=set()
    for batch in chunks(arts,200):
        d=api(ya,"POST",f"https://api.partner.market.yandex.ru/v2/campaigns/{YA_CAMPAIGN}/offers",{"offerIds":batch})
        for x in ((d.get("result") or {}).get("offers") or []):
            a=s(x.get("offerId"))
            if a:ya_existing.add(a)
    ya_prices=[]; ya_stocks=[]
    stamp=now()
    for art in ya_existing:
        r=by_article.get(art)
        if not r:continue
        sale=qty(r.get("Яндекс цена")); old=qty(r.get("Яндекс зачеркнутая цена"))
        if sale>0 and old>sale: ya_prices.append({"offerId":art,"price":{"value":sale,"currencyId":"RUR","discountBase":old}})
        ya_stocks.append({"sku":art,"partnerWarehouseId":YA_WAREHOUSE,"count":qty(r.get(FIELD_STOCK)),"updatedAt":stamp})
    ya_price_ok=0; ya_price_errors=[]
    for batch in chunks(ya_prices,200):
        d=api(ya,"POST",f"https://api.partner.market.yandex.ru/v2/businesses/{YA_BUSINESS}/offer-prices/updates",{"offers":batch})
        if s(d.get("status")).upper() in ("","OK") and not d.get("errors"): ya_price_ok+=len(batch)
        else: ya_price_errors.append(d)
    ya_stock_ok=0; ya_stock_errors=[]
    for batch in chunks(ya_stocks,200):
        try:
            d=api(ya,"POST",f"https://api.partner.market.yandex.ru/v3/businesses/{YA_BUSINESS}/offers/stocks/update",{"skuItems":batch})
            if s(d.get("status")).upper() in ("","OK") and not d.get("errors"): ya_stock_ok+=len(batch)
            else: ya_stock_errors.append(d)
        except Exception as e: ya_stock_errors.append({"error":str(e)[:1200]})
    report["yandex"]={"existing_offers":len(ya_existing),"prices_targeted":len(ya_prices),"prices_updated":ya_price_ok,"price_errors":ya_price_errors[:100],"stocks_targeted":len(ya_stocks),"stocks_updated":ya_stock_ok,"stock_errors":ya_stock_errors[:100],"partner_warehouse_id":YA_WAREHOUSE}

    report["finished_at"]=now()
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    # Keep the 6h job strict for price/stock transport errors.
    if wa_errors or kit_price_errors or oz_price_errors or oz_stock_errors or ya_price_errors or ya_stock_errors:
        return 1
    return 0
if __name__=="__main__": raise SystemExit(main())
