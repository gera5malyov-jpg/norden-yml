#!/usr/bin/env python3
from __future__ import annotations
import json, os, time
from datetime import datetime, timezone
from collections import defaultdict

import gspread, requests
from google.oauth2.service_account import Credentials

SID=os.environ["CATALOG_SPREADSHEET_ID"].strip()
SHEET=os.environ.get("CATALOG_SHEET","Норден").strip()
SA=os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
OZON_CLIENT_ID=os.environ["OZON_CLIENT_ID"].strip()
OZON_API_KEY=os.environ["OZON_API_KEY"].strip()
YANDEX_API_KEY=os.environ["YANDEX_MARKET_API_KEY"].strip()
PREFERRED_BUSINESS_ID=str(os.environ.get("YANDEX_MARKET_BUSINESS_ID") or "").strip()

OZON="https://api-seller.ozon.ru"
YANDEX="https://api.partner.market.yandex.ru"
NOW=datetime.now(timezone.utc).isoformat()

def s(v): return str(v or "").strip()
def f(v):
    try: return float(v or 0)
    except: return 0.0
def pct(amount,price):
    return round(amount/price*100,6) if amount>0 and price>0 else 0.0

def chunks(a,n):
    for i in range(0,len(a),n): yield a[i:i+n]

def call(session, method, url, *, body=None, params=None, retries=8):
    last=None
    for attempt in range(retries):
        r=session.request(method,url,json=body,params=params,timeout=90)
        last=r
        if r.status_code in (420,429) or r.status_code>=500:
            time.sleep(float(r.headers.get("Retry-After") or min(65,2**attempt)))
            continue
        try: data=r.json() if r.content else {}
        except Exception: data={"raw":r.text}
        if r.status_code>=400:
            raise RuntimeError(f"HTTP {r.status_code} {method} {url}: {json.dumps(data,ensure_ascii=False)[:1800]}")
        return data
    raise RuntimeError(f"request failed {method} {url}: {last.status_code if last else 'N/A'}")

oz=requests.Session()
oz.headers.update({"Client-Id":OZON_CLIENT_ID,"Api-Key":OZON_API_KEY,"Content-Type":"application/json","Accept":"application/json"})
ya=requests.Session()
ya.headers.update({"Api-Key":YANDEX_API_KEY,"Content-Type":"application/json","Accept":"application/json"})

def open_sheet():
    info=json.loads(SA)
    cr=Credentials.from_service_account_info(info,scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"])
    gc=gspread.authorize(cr)
    return gc.open_by_key(SID).worksheet(SHEET)

def ozon_prices(offer_ids):
    out={}
    for batch in chunks(offer_ids,100):
        d=call(oz,"POST",OZON+"/v5/product/info/prices",body={"cursor":"","filter":{"offer_id":batch,"visibility":"ALL"},"limit":100})
        for x in d.get("items") or []:
            oid=s(x.get("offer_id"))
            if oid: out[oid]=x
    return out

def yandex_campaigns():
    d=call(ya,"GET",YANDEX+"/v2/campaigns",params={"limit":100})
    rows=d.get("campaigns") or ((d.get("result") or {}).get("campaigns") if isinstance(d.get("result"),dict) else []) or []
    safe=[{
      "id":c.get("id"),
      "business_id":s((c.get("business") or {}).get("id") or c.get("businessId")),
      "business_name":s((c.get("business") or {}).get("name")),
      "domain":s(c.get("domain")),
      "placementType":s(c.get("placementType")),
      "apiAvailability":s(c.get("apiAvailability")),
    } for c in rows if isinstance(c,dict)]
    dbs=[c for c in rows if s(c.get("placementType")).upper()=="DBS" and s(c.get("apiAvailability")).upper() in ("","AVAILABLE")]
    preferred=[c for c in dbs if s((c.get("business") or {}).get("id") or c.get("businessId"))==PREFERRED_BUSINESS_ID]
    if preferred:
        dbs=preferred
    if not dbs:
        raise RuntimeError("Не найден доступный DBS-магазин. Кампании токена: "+json.dumps(safe,ensure_ascii=False))
    bids=sorted({s((c.get("business") or {}).get("id") or c.get("businessId")) for c in dbs if s((c.get("business") or {}).get("id") or c.get("businessId"))})
    if len(bids)!=1:
        raise RuntimeError("Найдено несколько business_id с DBS: "+json.dumps({"business_ids":bids,"campaigns":safe},ensure_ascii=False))
    return bids[0],dbs

def campaign_offers(cid):
    out={}; token=""
    while True:
        params={"limit":200}
        if token: params["pageToken"]=token
        d=call(ya,"POST",f"{YANDEX}/v2/campaigns/{cid}/offers",body={},params=params)
        res=d.get("result") or {}
        for x in res.get("offers") or []:
            oid=s(x.get("offerId"))
            if oid: out[oid]=x
        nt=s((res.get("paging") or {}).get("nextPageToken"))
        if not nt or nt==token: break
        token=nt
    return out

def offer_mappings(bid):
    out={}; token=""
    while True:
        params={"limit":100}
        if token: params["pageToken"]=token
        d=call(ya,"POST",f"{YANDEX}/v2/businesses/{bid}/offer-mappings",body={},params=params)
        res=d.get("result") or {}
        for x in res.get("offerMappings") or []:
            off=x.get("offer") or {}
            oid=s(off.get("offerId"))
            if oid: out[oid]=off
        nt=s((res.get("paging") or {}).get("nextPageToken"))
        if not nt or nt==token: break
        token=nt
    return out

def tariff_rows(cid, items):
    out={}
    for batch in chunks(items,200):
        req=[]
        ids=[]
        for oid,x in batch:
            req.append({"categoryId":int(x["categoryId"]),"price":x["price"],"length":x["length"],"width":x["width"],"height":x["height"],"weight":x["weight"],"quantity":1})
            ids.append(oid)
        d=call(ya,"POST",YANDEX+"/v2/tariffs/calculate",body={"parameters":{"campaignId":int(cid),"currency":"RUR"},"offers":req})
        rr=((d.get("result") or {}).get("offers") or [])
        if len(rr)!=len(ids):
            raise RuntimeError(f"Yandex tariff response length mismatch campaign {cid}: {len(rr)} != {len(ids)}")
        for oid,res in zip(ids,rr):
            out[oid]=res
    return out

def main():
    ws=open_sheet()
    vals=ws.get_all_values()
    headers=[s(x) for x in vals[0]]
    idx={h:i for i,h in enumerate(headers)}
    required=["Артикул","Ozon product_id","Ozon offer_id","Ozon sale_schema","Ozon комиссия %","Ozon эквайринг %","Ozon цена","Ozon старая цена","Ozon статус","Ozon дата",
              "Yandex offer_id","Yandex business_id","Yandex campaign_id","Yandex category_id","Yandex модель размещения","Yandex tariffs JSON","Yandex комиссия %","Yandex эквайринг %","Yandex цена","Yandex статус","Yandex дата"]
    miss=[x for x in required if x not in idx]
    if miss: raise RuntimeError("В листе нет колонок: "+", ".join(miss))

    rows=[]
    for rn,row in enumerate(vals[1:],2):
        art=s(row[idx["Артикул"]] if idx["Артикул"]<len(row) else "")
        if art: rows.append((rn,art))
    offers=sorted({a for _,a in rows})
    report={"started_at":NOW,"sheet_rows_with_article":len(rows),"unique_articles":len(offers),"ozon":{},"yandex":{}}

    # OZON: фактическая комиссия именно rFBS/RFBS.
    ozmap=ozon_prices(offers)
    report["ozon"]["found"]=len(ozmap)
    updates=[]
    for rn,art in rows:
        x=ozmap.get(art)
        if not x:
            for col,val in [("Ozon offer_id",art),("Ozon sale_schema","RFBS"),("Ozon статус","NOT_FOUND"),("Ozon дата",NOW)]:
                updates.append({"range":gspread.utils.rowcol_to_a1(rn,idx[col]+1),"values":[[val]]})
            continue
        comm=x.get("commissions") or {}
        op=x.get("price") or {}
        cur=f(op.get("marketing_seller_price") or op.get("price"))
        acquiring=f(x.get("acquiring"))
        acqp=pct(acquiring,cur)
        pairs=[
          ("Ozon product_id",s(x.get("product_id"))),("Ozon offer_id",art),("Ozon sale_schema","RFBS"),
          ("Ozon комиссия %",f(comm.get("sales_percent_rfbs"))),("Ozon эквайринг %",acqp),
          ("Ozon цена",cur),("Ozon старая цена",f(op.get("old_price"))),("Ozon статус","FOUND"),("Ozon дата",NOW)
        ]
        for col,val in pairs: updates.append({"range":gspread.utils.rowcol_to_a1(rn,idx[col]+1),"values":[[val]]})

    # YANDEX: только реальный DBS-магазин, свой склад + своя доставка.
    bid,dbs=yandex_campaigns()
    report["yandex"]["business_id"]=bid
    report["yandex"]["dbs_campaigns"]=[{"id":c.get("id"),"domain":c.get("domain"),"placementType":c.get("placementType")} for c in dbs]
    by_offer=defaultdict(list)
    campaign_data={}
    for c in dbs:
        cid=s(c.get("id"))
        co=campaign_offers(cid); campaign_data[cid]=co
        for oid in co: by_offer[oid].append(cid)
    mappings=offer_mappings(bid)

    tariff_input=defaultdict(list)
    meta={}
    for art in offers:
        cids=by_offer.get(art,[])
        if len(cids)!=1: continue
        cid=cids[0]; co=campaign_data[cid][art]; mp=mappings.get(art) or {}
        price=f((co.get("campaignPrice") or {}).get("value") or (co.get("basicPrice") or {}).get("value"))
        wd=mp.get("weightDimensions") or {}
        category=int(mp.get("marketCategoryId") or 0)
        length=f(wd.get("length")); width=f(wd.get("width")); height=f(wd.get("height")); weight=f(wd.get("weight"))
        meta[art]={"cid":cid,"price":price,"category":category}
        if category>0 and price>0 and length>0 and width>0 and height>0 and weight>0:
            tariff_input[cid].append((art,{"categoryId":category,"price":price,"length":length,"width":width,"height":height,"weight":weight}))

    tariffs={}
    for cid,items in tariff_input.items(): tariffs.update(tariff_rows(cid,items))
    report["yandex"]["offers_in_dbs"]=len(by_offer)
    report["yandex"]["tariffs_calculated"]=len(tariffs)

    for rn,art in rows:
        cids=by_offer.get(art,[])
        base=[("Yandex offer_id",art),("Yandex business_id",bid),("Yandex модель размещения","DBS"),("Yandex дата",NOW)]
        if len(cids)==0:
            base += [("Yandex статус","NOT_FOUND")]
        elif len(cids)>1:
            base += [("Yandex статус","AMBIGUOUS_CAMPAIGN")]
        else:
            cid=cids[0]; m=meta.get(art) or {}
            base += [("Yandex campaign_id",cid),("Yandex category_id",m.get("category") or ""),("Yandex цена",m.get("price") or 0)]
            tr=tariffs.get(art)
            if tr:
                services=tr.get("tariffs") or []
                fee=sum(f(t.get("amount")) for t in services if s(t.get("type"))=="FEE")
                pay=sum(f(t.get("amount")) for t in services if s(t.get("type")) in ("AGENCY_COMMISSION","PAYMENT_TRANSFER"))
                price=f(m.get("price"))
                base += [
                  ("Yandex tariffs JSON",json.dumps(services,ensure_ascii=False,separators=(",",":"))),
                  ("Yandex комиссия %",pct(fee,price)),
                  ("Yandex эквайринг %",pct(pay,price)),
                  ("Yandex статус","FOUND")
                ]
            else:
                base += [("Yandex статус","NO_TARIFF_INPUT")]
        for col,val in base: updates.append({"range":gspread.utils.rowcol_to_a1(rn,idx[col]+1),"values":[[val]]})

    for batch in chunks(updates,250):
        ws.batch_update(batch,value_input_option="RAW")
        time.sleep(0.2)

    report["ozon"]["sheet_found_rows"]=sum(1 for _,a in rows if a in ozmap)
    report["yandex"]["sheet_found_rows"]=sum(1 for _,a in rows if len(by_offer.get(a,[]))==1)
    report["finished_at"]=datetime.now(timezone.utc).isoformat()
    with open("catalog/norden_marketplace_commissions_report.json","w",encoding="utf-8") as fh:
        json.dump(report,fh,ensure_ascii=False,indent=2); fh.write("\n")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
