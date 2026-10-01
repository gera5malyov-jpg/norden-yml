#!/usr/bin/env python3
from __future__ import annotations
import json, math, os, unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import requests

BASEROW_URL=os.environ.get("BASEROW_URL","http://147.78.67.6").rstrip("/")
TOKEN=os.environ.get("BASEROW_DATABASE_TOKEN","").strip()
CATALOG=156; SUPPLIERS=157; SUPPLIER="Norden"
REPORT=Path("baserow/norden_price_calculation_report.json")

FIELDS={
 "purchase":"Закупка Norden",
 "commission_ozon":"Комиссия Ozon",
 "commission_yandex":"Комиссия Яндекс",
 "wa_sale":"Цена продажи Webasyst",
 "wa_old":"Цена зачеркнутая Webasyst",
 "kit_old":"Цена KIT до скидки",
 "kit_sale":"Цена KIT со скидкой",
 "ya_sale":"Яндекс цена",
 "ya_old":"Яндекс зачеркнутая цена",
 "oz_sale":"Ozon Предельная цена без акций",
 "oz_old":"Ozon Зачёркнутая цена",
 "oz_min":"Ограничение для акций и стратегий",
}

def s(v): return str(v or "").strip()
def norm(v): return unicodedata.normalize("NFKC",s(v)).casefold()
def num(v):
    try:return float(str(v).replace("\xa0","").replace(" ","").replace(",","."))
    except:return 0.0
def ceil_rub(v): return int(math.ceil(float(v)-1e-9))
def round2(v): return round(float(v)+1e-10,2)
def now(): return datetime.now(timezone.utc).isoformat()

class BR:
    def __init__(self):
        if not TOKEN: raise RuntimeError("BASEROW_DATABASE_TOKEN missing")
        self.ses=requests.Session(); self.ses.headers.update({"Authorization":"Token "+TOKEN,"Accept":"application/json","Content-Type":"application/json"})
    def req(self,m,p,body=None):
        r=self.ses.request(m,BASEROW_URL+p,json=body,timeout=90)
        if not r.ok: raise RuntimeError(f"Baserow {m} {p}: HTTP {r.status_code}: {r.text[:1200]}")
        return r.json() if r.content else {}
    def rows(self,tid):
        out=[]; page=1
        while True:
            d=self.req("GET",f"/api/database/rows/table/{tid}/?user_field_names=true&size=200&page={page}")
            out.extend(d.get("results") or [])
            if not d.get("next"): return out
            page+=1
    def batch(self,items):
        for i in range(0,len(items),100):
            self.req("PATCH",f"/api/database/rows/table/{CATALOG}/batch/?user_field_names=true",{"items":items[i:i+100]})

def supplier_ids(row):
    out=set()
    for x in row.get("Поставщик") or []:
        if isinstance(x,dict) and x.get("id") is not None:
            try: out.add(int(x["id"]))
            except: pass
    return out

def with_commission(purchase,commission,markup):
    c=float(commission)
    denom=1.0-c/100.0
    if purchase<=0 or c<=0 or denom<=0: return None
    return float(purchase)*(1.0+float(markup)/100.0)/denom

def main():
    br=BR()
    suppliers=br.rows(SUPPLIERS)
    matches=[r for r in suppliers if norm(r.get("Поставщик"))==norm(SUPPLIER)]
    if len(matches)!=1: raise RuntimeError(f"Expected one Norden supplier, got {len(matches)}")
    sid=int(matches[0]["id"])
    rows=[r for r in br.rows(CATALOG) if sid in supplier_ids(r) or norm(r.get("Бренд"))=="norden"]
    updates=[]; no_purchase=0; no_oz_comm=0; no_ya_comm=0
    for r in rows:
        p=num(r.get(FIELDS["purchase"]))
        if p<=0:
            no_purchase+=1; continue
        body={"id":r["id"]}
        # Webasyst / KIT: markup on purchase.
        body[FIELDS["wa_sale"]]=round2(p*1.25)
        body[FIELDS["wa_old"]]=round2(p*1.60)
        body[FIELDS["kit_old"]]=round2(p*1.60)
        body[FIELDS["kit_sale"]]=round2(p*1.25)

        co=num(r.get(FIELDS["commission_ozon"]))
        if co>0:
            v=with_commission(p,co,21); old=with_commission(p,co,60); mn=with_commission(p,co,18)
            if v and old and mn:
                body[FIELDS["oz_sale"]]=ceil_rub(v)
                body[FIELDS["oz_old"]]=ceil_rub(old)
                body[FIELDS["oz_min"]]=ceil_rub(mn)
        else:
            no_oz_comm+=1

        cy=num(r.get(FIELDS["commission_yandex"]))
        if cy>0:
            v=with_commission(p,cy,21); old=with_commission(p,cy,60)
            if v and old:
                body[FIELDS["ya_sale"]]=ceil_rub(v)
                body[FIELDS["ya_old"]]=ceil_rub(old)
        else:
            no_ya_comm+=1
        updates.append(body)
    if updates: br.batch(updates)
    report={
      "started_at":now(),"norden_rows":len(rows),"updated_rows":len(updates),
      "rows_without_purchase":no_purchase,"rows_without_ozon_commission":no_oz_comm,
      "rows_without_yandex_commission":no_ya_comm,
      "formulas":{
        FIELDS["wa_sale"]:"Закупка Norden × 1.25",
        FIELDS["wa_old"]:"Закупка Norden × 1.60",
        FIELDS["kit_old"]:"Закупка Norden × 1.60",
        FIELDS["kit_sale"]:"Закупка Norden × 1.25",
        FIELDS["ya_sale"]:"Закупка Norden × 1.21 / (1 - Комиссия Яндекс/100)",
        FIELDS["ya_old"]:"Закупка Norden × 1.60 / (1 - Комиссия Яндекс/100)",
        FIELDS["oz_sale"]:"Закупка Norden × 1.21 / (1 - Комиссия Ozon/100)",
        FIELDS["oz_old"]:"Закупка Norden × 1.60 / (1 - Комиссия Ozon/100)",
        FIELDS["oz_min"]:"Закупка Norden × 1.18 / (1 - Комиссия Ozon/100)",
      },
      "rounding":"Webasyst/KIT: 2 decimals; Ozon/Yandex: round upward to whole RUB",
      "finished_at":now()
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
