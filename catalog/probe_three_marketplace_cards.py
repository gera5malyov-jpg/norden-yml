#!/usr/bin/env python3
import json, os, requests
from pathlib import Path
OZ="https://api-seller.ozon.ru"; YA="https://api.partner.market.yandex.ru"; BID="20806099"
arts=["AF-31662372","AF-31662420","AF-31662421"]
oh={"Client-Id":os.environ["OZON_CLIENT_ID"],"Api-Key":os.environ["OZON_API_KEY"],"Content-Type":"application/json"}
yh={"Api-Key":os.environ["YANDEX_MARKET_API_KEY"],"Content-Type":"application/json"}
out={}
for a in arts:
    ro=requests.post(OZ+"/v3/product/list",headers=oh,json={"filter":{"offer_id":[a],"visibility":"ALL"},"limit":100},timeout=120)
    oy={}
    try: oy=ro.json()
    except: oy={"raw":ro.text}
    ry=requests.post(YA+f"/v2/businesses/{BID}/offer-mappings",headers=yh,json={"offerIds":[a]},timeout=120)
    yy={}
    try: yy=ry.json()
    except: yy={"raw":ry.text}
    out[a]={"ozon_status":ro.status_code,"ozon":oy,"yandex_status":ry.status_code,"yandex":yy}
Path("catalog/three_marketplace_existence_probe.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({a:{"ozon_count":len((((v["ozon"].get("result") or {}).get("items")) or [])),"yandex_count":len((((v["yandex"].get("result") or {}).get("offerMappings")) or []))} for a,v in out.items()},ensure_ascii=False))
