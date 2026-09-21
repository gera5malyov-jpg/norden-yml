#!/usr/bin/env python3
import os, json, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

TOKEN=os.environ.get("WB_API_TOKEN","").strip()
NMID=466513948
if not TOKEN:
    raise SystemExit("WB_API_TOKEN_MISSING=1")

HEAD={"Authorization":TOKEN,"Content-Type":"application/json"}

def call(method,url,body=None,timeout=60):
    data=None if body is None else json.dumps(body,ensure_ascii=False).encode("utf-8")
    req=urllib.request.Request(url,data=data,method=method,headers=HEAD)
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:
            raw=r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw=e.read().decode("utf-8","replace")
        try: obj=json.loads(raw) if raw else None
        except: obj=None
        return e.code,obj
    except Exception:
        return 0,None

def out(k,v):
    if v is None: v=""
    print(f"{k}={v}")

# 1) Card data
card=None
st,obj=call("POST","https://content-api.wildberries.ru/content/v2/get/cards/list?locale=ru",{
    "settings":{
        "sort":{"ascending":False},
        "filter":{"textSearch":str(NMID),"withPhoto":-1},
        "cursor":{"limit":10}
    }
})
out("WB_CONTENT_HTTP",st)
if st==200 and isinstance(obj,dict):
    for c in obj.get("cards") or []:
        if int(c.get("nmID") or 0)==NMID:
            card=c; break
if card:
    out("WB_SUBJECT_ID",card.get("subjectID"))
    out("WB_SUBJECT_NAME",card.get("subjectName"))
    out("WB_TITLE",card.get("title"))
    d=card.get("dimensions") or {}
    out("WB_LENGTH_CM",d.get("length"))
    out("WB_WIDTH_CM",d.get("width"))
    out("WB_HEIGHT_CM",d.get("height"))
    out("WB_WEIGHT_KG",d.get("weightBrutto"))
    sizes=card.get("sizes") or []
    if sizes:
        out("WB_CHRT_ID",(sizes[0] or {}).get("chrtID"))
else:
    out("WB_CARD_FOUND",0)

# 2) Current commissions
st,obj=call("GET","https://common-api.wildberries.ru/api/v1/tariffs/commission?locale=ru")
out("WB_COMMISSION_HTTP",st)
commission=None
if st==200 and isinstance(obj,dict):
    report=obj.get("report") or []
    sid=int(card.get("subjectID") or 0) if card else 0
    for x in report:
        if sid and int(x.get("subjectID") or 0)==sid:
            commission=x; break
    if commission is None:
        for x in report:
            n=str(x.get("subjectName") or "").lower()
            if "кроват" in n:
                commission=x; break
if commission:
    out("WB_COMMISSION_SUBJECT",commission.get("subjectName"))
    out("WB_COMMISSION_FBW",commission.get("paidStorageKgvp"))
    out("WB_COMMISSION_FBS",commission.get("kgvpMarketplace"))
    out("WB_COMMISSION_DBS",commission.get("kgvpSupplier"))
    out("WB_COMMISSION_EDBS",commission.get("kgvpSupplierExpress"))

# 3) FBS warehouses + current stock for this size
chrt=None
if card and card.get("sizes"):
    chrt=(card["sizes"][0] or {}).get("chrtID")
st,warehouses=call("GET","https://marketplace-api.wildberries.ru/api/v3/warehouses")
out("WB_MARKETPLACE_HTTP",st)
stock_wh=[]
if st==200 and isinstance(warehouses,list) and chrt:
    for w in warehouses:
        wid=w.get("id")
        sst,sobj=call("POST",f"https://marketplace-api.wildberries.ru/api/v3/stocks/{wid}",{"chrtIds":[int(chrt)]})
        amt=0
        if sst==200 and isinstance(sobj,dict):
            rows=sobj.get("stocks") or []
            if rows:
                amt=int((rows[0] or {}).get("amount") or 0)
        if amt>0:
            stock_wh.append({"name":w.get("name"),"officeId":w.get("officeId"),"cargoType":w.get("cargoType"),"deliveryType":w.get("deliveryType"),"amount":amt})
    out("WB_FBS_STOCK_WAREHOUSES",len(stock_wh))
    for i,w in enumerate(stock_wh[:10],1):
        out(f"WB_FBS_WAREHOUSE_{i}",w.get("name"))
        out(f"WB_FBS_STOCK_{i}",w.get("amount"))
        out(f"WB_FBS_CARGO_TYPE_{i}",w.get("cargoType"))
        out(f"WB_FBS_OFFICE_ID_{i}",w.get("officeId"))

# 4) Operational sales, last 90 days — only sanitized matching values
date_from=(datetime.now(timezone.utc)-timedelta(days=90)).strftime("%Y-%m-%dT00:00:00Z")
st,sales=call("GET",f"https://statistics-api.wildberries.ru/api/v1/supplier/sales?dateFrom={date_from}&flag=0",timeout=120)
out("WB_STATS_HTTP",st)
matches=[]
if st==200 and isinstance(sales,list):
    matches=[x for x in sales if int(x.get("nmId") or 0)==NMID]
    matches.sort(key=lambda x:str(x.get("date") or x.get("lastChangeDate") or ""),reverse=True)
out("WB_SALES_90D",len(matches))
if matches:
    x=matches[0]
    out("WB_LAST_SALE_DATE",x.get("date"))
    out("WB_LAST_SALE_SUBJECT",x.get("subject"))
    out("WB_LAST_SALE_PRICE_WITH_DISC",x.get("priceWithDisc"))
    out("WB_LAST_SALE_BUYER_FINISHED_PRICE",x.get("finishedPrice"))
    out("WB_LAST_SALE_SPP",x.get("spp"))
    out("WB_LAST_SALE_FOR_PAY_PRELIM",x.get("forPay"))
    out("WB_LAST_SALE_PAYMENT_AMOUNT",x.get("paymentSaleAmount"))

# 5) Finance detailed report, last 90 days, requested fields only.
# Accurate/reconciled source per WB docs. No order IDs or personal data are printed.
today=datetime.now(timezone.utc).date()
body={
  "dateFrom":str(today-timedelta(days=90)),
  "dateTo":str(today),
  "limit":100000,
  "rrdId":0,
  "period":"weekly",
  "fields":[
    "rrdId","nmId","docTypeName","quantity","retailPrice","retailAmount",
    "commissionPercent","retailPriceWithDisc","deliveryService",
    "productDiscountForReport","spp","kvwBase","kvw","ppvzSalesCommission",
    "forPay","acquiringFee","acquiringPercent","paidStorage","deduction","penalty",
    "paidAcceptance","deliveryMethod","saleDt","orderDt","sellerOperName",
    "officeName","warehouseLogisticsCoeff"
  ]
}
st,fin=call("POST","https://finance-api.wildberries.ru/api/finance/v1/sales-reports/detailed",body,timeout=180)
out("WB_FINANCE_HTTP",st)
fm=[]
if st==200 and isinstance(fin,list):
    fm=[x for x in fin if int(x.get("nmId") or 0)==NMID]
    fm.sort(key=lambda x:str(x.get("saleDt") or x.get("orderDt") or ""),reverse=True)
out("WB_FINANCE_ROWS_90D",len(fm))
if fm:
    # Print up to 12 newest rows; these can include sale + logistics rows.
    for i,x in enumerate(fm[:12],1):
        out(f"WB_FIN_{i}_SALE_DT",x.get("saleDt"))
        out(f"WB_FIN_{i}_OPER",x.get("sellerOperName"))
        out(f"WB_FIN_{i}_DOC",x.get("docTypeName"))
        out(f"WB_FIN_{i}_DELIVERY_METHOD",x.get("deliveryMethod"))
        out(f"WB_FIN_{i}_COMMISSION_PCT",x.get("commissionPercent"))
        out(f"WB_FIN_{i}_RETAIL_WITH_DISC",x.get("retailPriceWithDisc"))
        out(f"WB_FIN_{i}_RETAIL_AMOUNT",x.get("retailAmount"))
        out(f"WB_FIN_{i}_SPP",x.get("spp"))
        out(f"WB_FIN_{i}_KVW_BASE",x.get("kvwBase"))
        out(f"WB_FIN_{i}_KVW",x.get("kvw"))
        out(f"WB_FIN_{i}_SALES_COMMISSION",x.get("ppvzSalesCommission"))
        out(f"WB_FIN_{i}_FOR_PAY",x.get("forPay"))
        out(f"WB_FIN_{i}_ACQ_FEE",x.get("acquiringFee"))
        out(f"WB_FIN_{i}_ACQ_PCT",x.get("acquiringPercent"))
        out(f"WB_FIN_{i}_DELIVERY_SERVICE",x.get("deliveryService"))
        out(f"WB_FIN_{i}_PAID_STORAGE",x.get("paidStorage"))
        out(f"WB_FIN_{i}_PAID_ACCEPTANCE",x.get("paidAcceptance"))
        out(f"WB_FIN_{i}_DEDUCTION",x.get("deduction"))
        out(f"WB_FIN_{i}_PENALTY",x.get("penalty"))
        out(f"WB_FIN_{i}_LOG_COEFF",x.get("warehouseLogisticsCoeff"))
