#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

BASEROW_URL=os.environ.get("BASEROW_URL","http://147.78.67.6").rstrip("/")
BASEROW_TOKEN=os.environ["BASEROW_DATABASE_TOKEN"].strip()
OZON_CLIENT_ID=os.environ["OZON_CLIENT_ID"].strip()
OZON_API_KEY=os.environ["OZON_API_KEY"].strip()
YANDEX_API_KEY=os.environ["YANDEX_MARKET_API_KEY"].strip()
YANDEX_CAMPAIGN_ID=str(os.environ.get("YANDEX_MARKET_CAMPAIGN_ID") or "89405839").strip()
TABLE_ID=156
FIELD_OZON="Загружать в Ozon"
FIELD_YANDEX="Загружать в Яндекс"
REPORT=Path("baserow/marketplace_flags_bootstrap_report.json")

def s(v): return str(v or "").strip()

def chunks(rows,size):
    for i in range(0,len(rows),size):
        yield rows[i:i+size]

def call(session,method,url,*,body=None,retries=8):
    last=None
    for attempt in range(retries):
        try:
            r=session.request(method,url,json=body,timeout=90)
        except requests.RequestException as exc:
            last=exc
            time.sleep(min(20,2**attempt))
            continue
        last=r
        if r.status_code in (420,429) or r.status_code>=500:
            time.sleep(float(r.headers.get("Retry-After") or min(30,2**attempt)))
            continue
        if not r.ok:
            raise RuntimeError(f"{method} {url} HTTP {r.status_code}: {r.text[:1200]}")
        return r.json() if r.content else {}
    raise RuntimeError(f"request failed {method} {url}: {last}")

class Baserow:
    def __init__(self):
        self.session=requests.Session()
        self.session.headers.update({
            "Authorization":f"Token {BASEROW_TOKEN}",
            "Accept":"application/json",
            "Content-Type":"application/json",
        })
    def get(self,path):
        return call(self.session,"GET",BASEROW_URL+path)
    def rows(self):
        out=[]; page=1
        while True:
            d=self.get(f"/api/database/rows/table/{TABLE_ID}/?user_field_names=true&size=200&page={page}")
            out.extend(d.get("results") or [])
            if not d.get("next"): return out
            page+=1
    def batch_update(self,items):
        for batch in chunks(items,100):
            call(self.session,"PATCH",BASEROW_URL+f"/api/database/rows/table/{TABLE_ID}/batch/?user_field_names=true",body={"items":batch})

def main():
    br=Baserow()
    rows=br.rows()
    by_offer={}
    for row in rows:
        offer=s(row.get("Артикул"))
        if offer:
            by_offer.setdefault(offer,[]).append(row)
    offers=sorted(by_offer)

    oz=requests.Session()
    oz.headers.update({
        "Client-Id":OZON_CLIENT_ID,
        "Api-Key":OZON_API_KEY,
        "Accept":"application/json",
        "Content-Type":"application/json",
    })
    oz_found=set()
    for batch in chunks(offers,100):
        d=call(oz,"POST","https://api-seller.ozon.ru/v5/product/info/prices",
               body={"cursor":"","filter":{"offer_id":batch,"visibility":"ALL"},"limit":100})
        for x in d.get("items") or []:
            offer=s(x.get("offer_id"))
            if offer:
                oz_found.add(offer)

    ya=requests.Session()
    ya.headers.update({
        "Api-Key":YANDEX_API_KEY,
        "Accept":"application/json",
        "Content-Type":"application/json",
    })
    ya_found=set()
    for batch in chunks(offers,200):
        d=call(ya,"POST",f"https://api.partner.market.yandex.ru/v2/campaigns/{YANDEX_CAMPAIGN_ID}/offers",
               body={"offerIds":batch})
        for x in ((d.get("result") or {}).get("offers") or []):
            offer=s(x.get("offerId"))
            if offer:
                ya_found.add(offer)

    updates=[]
    oz_rows=ya_rows=both_rows=0
    for offer,matched in by_offer.items():
        ozon=offer in oz_found
        yandex=offer in ya_found
        for row in matched:
            patch={"id":row["id"]}
            changed=False
            if ozon and row.get(FIELD_OZON) is not True:
                patch[FIELD_OZON]=True
                changed=True
            if yandex and row.get(FIELD_YANDEX) is not True:
                patch[FIELD_YANDEX]=True
                changed=True
            if changed:
                updates.append(patch)
            if ozon: oz_rows+=1
            if yandex: ya_rows+=1
            if ozon and yandex: both_rows+=1

    if updates:
        br.batch_update(updates)

    verify=br.rows()
    report={
        "rows_total":len(verify),
        "offers_checked":len(offers),
        "ozon_offers_found":len(oz_found),
        "yandex_offers_found":len(ya_found),
        "ozon_rows_marked_source":oz_rows,
        "yandex_rows_marked_source":ya_rows,
        "both_rows_marked_source":both_rows,
        "rows_updated":len(updates),
        "checked_ozon_after":sum(1 for r in verify if r.get(FIELD_OZON) is True),
        "checked_yandex_after":sum(1 for r in verify if r.get(FIELD_YANDEX) is True),
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
