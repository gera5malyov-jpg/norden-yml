#!/usr/bin/env python3
import json, os, traceback, math
from datetime import datetime, timezone
import requests

OUT="cdek-tolyatti-quote-result.json"
result={"generated_at":datetime.now(timezone.utc).isoformat(),"status":"ОШИБКА"}

try:
    CLIENT_ID=os.getenv("CDEK_CLIENT_ID","").strip()
    CLIENT_SECRET=os.getenv("CDEK_CLIENT_SECRET","").strip()
    BASE="https://api.cdek.ru/v2"
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError("CDEK_CLIENT_ID/CDEK_CLIENT_SECRET are missing")

    s=requests.Session()
    s.headers.update({"User-Agent":"megapolis-cdek-quote/1.0","Accept":"application/json"})
    r=s.post(BASE+"/oauth/token", data={
        "grant_type":"client_credentials",
        "client_id":CLIENT_ID,
        "client_secret":CLIENT_SECRET,
    }, timeout=60)
    r.raise_for_status()
    token=r.json()["access_token"]
    s.headers.update({"Authorization":"Bearer "+token,"Content-Type":"application/json"})

    def get_city(name):
        rr=s.get(BASE+"/location/cities", params={"city":name,"country_codes":"RU","size":100}, timeout=60)
        rr.raise_for_status()
        data=rr.json()
        exact=[x for x in data if str(x.get("city","")).casefold()==name.casefold()]
        if exact: return exact[0]
        if data: return data[0]
        raise RuntimeError("City not found: "+name)

    spb=get_city("Санкт-Петербург")
    moscow=get_city("Москва")

    packages=[{"weight":132,"length":5,"width":14,"height":11} for _ in range(12)]
    base_body={
      "type":1,
      "from_location":{"code":spb["code"]},
      "to_location":{"code":moscow["code"]},
      "packages":packages,
      "lang":"rus",
      "currency":1
    }

    rr=s.post(BASE+"/calculator/tarifflist", json=base_body, timeout=60)
    rr.raise_for_status()
    payload=rr.json()
    wh=[t for t in (payload.get("tariff_codes") or []) if t.get("delivery_mode")==4]

    tariffs=[]
    for t in wh:
        body=dict(base_body)
        body["tariff_code"]=t["tariff_code"]
        body["services"]=[{"code":"INSURANCE","parameter":"2100"}]
        tr=s.post(BASE+"/calculator/tariff", json=body, timeout=60)
        try:
            detail=tr.json()
        except Exception:
            detail={"raw":tr.text[:2000]}
        tariffs.append({
          "tariff_code":t.get("tariff_code"),
          "tariff_name":t.get("tariff_name"),
          "tariff_description":t.get("tariff_description"),
          "delivery_mode":t.get("delivery_mode"),
          "list_delivery_sum":t.get("delivery_sum"),
          "list_period_min":t.get("period_min"),
          "list_period_max":t.get("period_max"),
          "http":tr.status_code,
          "delivery_sum":detail.get("delivery_sum") if isinstance(detail,dict) else None,
          "total_sum":detail.get("total_sum") if isinstance(detail,dict) else None,
          "period_min":detail.get("period_min") if isinstance(detail,dict) else None,
          "period_max":detail.get("period_max") if isinstance(detail,dict) else None,
          "weight_calc":detail.get("weight_calc") if isinstance(detail,dict) else None,
          "services":detail.get("services") if isinstance(detail,dict) else None,
          "errors":detail.get("errors") if isinstance(detail,dict) else None,
          "warnings":detail.get("warnings") if isinstance(detail,dict) else None,
        })

    tariffs.sort(key=lambda x: (
      x.get("total_sum") is None,
      x.get("total_sum") if x.get("total_sum") is not None else 10**18,
      x.get("delivery_sum") if x.get("delivery_sum") is not None else 10**18
    ))

    result={
      "generated_at":datetime.now(timezone.utc).isoformat(),
      "status":"УСПЕШНО",
      "route":{
        "from":{"code":spb.get("code"),"city":spb.get("city"),"region":spb.get("region")},
        "to":{"code":moscow.get("code"),"city":moscow.get("city"),"region":moscow.get("region")},
        "mode":"склад-склад"
      },
      "cargo":{
        "places":12,
        "each_original_cm":[4.2,13.9,10.9],
        "each_sent_to_api_cm":[5,14,11],
        "each_weight_kg":0.132,
        "total_weight_kg":1.584,
        "declared_value_rub":2100,
        "cod_rub":0
      },
      "tariffs":tariffs
    }

except Exception as e:
    result["status"]="ОШИБКА"
    result["error"]=str(e)
    result["trace"]=traceback.format_exc(limit=3)

with open(OUT,"w",encoding="utf-8") as f:
    json.dump(result,f,ensure_ascii=False,indent=2)
print(json.dumps(result,ensure_ascii=False,indent=2))
