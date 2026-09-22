#!/usr/bin/env python3
import json, os, traceback
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
    if not r.ok:
        raise RuntimeError(f"OAuth HTTP {r.status_code}: {r.text[:1000]}")
    token=r.json().get("access_token")
    if not token:
        raise RuntimeError("OAuth response has no access_token")
    s.headers.update({"Authorization":"Bearer "+token})

    def get_city(name):
        rr=s.get(BASE+"/location/cities", params={"city":name,"country_codes":"RU","size":100}, timeout=60)
        if not rr.ok:
            raise RuntimeError(f"Cities HTTP {rr.status_code}: {rr.text[:1000]}")
        data=rr.json()
        exact=[x for x in data if str(x.get("city","")).casefold()==name.casefold()]
        if exact:
            return exact[0]
        if data:
            return data[0]
        raise RuntimeError("City not found: "+name)

    moscow=get_city("Москва")
    tolyatti=get_city("Тольятти")

    scenarios=[
      ("working_053",58,60,152,64000),
      ("conservative_0643",58,60,185,64000),
    ]

    result={
      "generated_at":datetime.now(timezone.utc).isoformat(),
      "status":"УСПЕШНО",
      "route":{
        "from":{"code":moscow.get("code"),"city":moscow.get("city"),"region":moscow.get("region")},
        "to":{"code":tolyatti.get("code"),"city":tolyatti.get("city"),"region":tolyatti.get("region")},
      },
      "cargo":{
        "chairs":30,
        "places":3,
        "chairs_per_place":10,
        "weight_per_chair_kg":6.4,
        "weight_per_place_kg":64,
        "total_weight_kg":192,
      },
      "quotes":[]
    }

    for name,l,w,h,weight in scenarios:
        packages=[{"weight":weight,"length":l,"width":w,"height":h} for _ in range(3)]
        body={
          "type":1,
          "from_location":{"code":moscow["code"]},
          "to_location":{"code":tolyatti["code"]},
          "packages":packages,
          "lang":"rus",
        }
        rr=s.post(BASE+"/calculator/tarifflist", json=body, timeout=60)
        try:
            payload=rr.json()
        except Exception:
            payload={"raw":rr.text[:2000]}
        entry={
          "scenario":name,
          "http":rr.status_code,
          "package_cm":[l,w,h],
          "package_weight_kg":weight/1000,
          "total_volume_m3":round((l*w*h/1_000_000)*3,3),
          "tariffs":[]
        }
        if rr.ok and isinstance(payload,dict):
            tariffs=payload.get("tariff_codes") or []
            for t in tariffs:
                entry["tariffs"].append({
                  "tariff_code":t.get("tariff_code"),
                  "tariff_name":t.get("tariff_name"),
                  "tariff_description":t.get("tariff_description"),
                  "delivery_mode":t.get("delivery_mode"),
                  "delivery_sum":t.get("delivery_sum"),
                  "period_min":t.get("period_min"),
                  "period_max":t.get("period_max"),
                  "calendar_min":t.get("calendar_min"),
                  "calendar_max":t.get("calendar_max"),
                  "services":t.get("services"),
                })
        else:
            entry["error"]=payload
        result["quotes"].append(entry)

except Exception as e:
    result["status"]="ОШИБКА"
    result["error"]=str(e)
    result["trace"]=traceback.format_exc(limit=3)

with open(OUT,"w",encoding="utf-8") as f:
    json.dump(result,f,ensure_ascii=False,indent=2)
print(json.dumps(result,ensure_ascii=False,indent=2))
