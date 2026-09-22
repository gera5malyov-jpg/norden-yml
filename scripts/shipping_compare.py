#!/usr/bin/env python3
import base64, json, os, sys, urllib.parse, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

REQ_PATH = "shipping-quote/request.json"
OUT_PATH = "shipping-quote/result.json"

with open(REQ_PATH, encoding="utf-8") as f:
    req = json.load(f)

result = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "request": req,
    "carriers": {}
}

def jdump(x):
    return json.dumps(x, ensure_ascii=False)

def http_json(url, *, method="GET", headers=None, data=None, timeout=60):
    hdrs = {"User-Agent":"megapolis-shipping-quote/1.0","Accept":"application/json"}
    if headers: hdrs.update(headers)
    body = None
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            hdrs.setdefault("Content-Type","application/json; charset=utf-8")
        else:
            body = data
    r = urllib.request.Request(url, data=body, method=method, headers=hdrs)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
        return resp.status, json.loads(raw)

def safe(name, fn):
    try:
        result["carriers"][name] = fn()
    except Exception as e:
        result["carriers"][name] = {"status":"ОШИБКА","error":str(e)}

# ---------- e-bulky ----------
def quote_ebulky():
    key = os.environ.get("E_BULKY","").strip()
    if not key:
        raise RuntimeError("E_BULKY secret missing")
    payload = {
        "apikey": key,
        "nds": "false",
        "address": req["to_address"],
        "weight": str(req["weight_kg"]),
        "dimension_side1": str(req["width_cm"]),
        "dimension_side2": str(req["height_cm"]),
        "dimension_side3": str(req["length_cm"]),
        "floor": "1",
        "cargo_lift": "true",
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    status, ans = http_json(
        "https://api.e-bulky.ru/api/v1/public/calculate",
        method="POST",
        headers={"Content-Type":"application/x-www-form-urlencoded"},
        data=data,
    )
    response = ans.get("response") if isinstance(ans, dict) else None
    if not isinstance(response, dict):
        raise RuntimeError("unexpected calculate response: "+jdump(ans)[:1000])
    api_total = response.get("total")
    if api_total is None:
        raise RuntimeError("e-bulky response has no total: "+jdump(response)[:1000])
    api_total = float(api_total)
    pickup = float(req.get("e_bulky_pickup_fixed_rub",0))
    configured_mandatory = float(req.get("e_bulky_mandatory_service_rub",0))
    detail = response.get("detail") if isinstance(response.get("detail"), dict) else {}
    included_mandatory = float(detail.get("option_skid_kgt") or 0)
    mandatory_extra = max(0.0, configured_mandatory - included_mandatory)
    return {
        "status":"УСПЕШНО",
        "api_delivery_rub": round(api_total,2),
        "pickup_rub": round(pickup,2),
        "mandatory_service_included_rub": round(included_mandatory,2),
        "mandatory_service_extra_rub": round(mandatory_extra,2),
        "total_rub": round(api_total + pickup + mandatory_extra,2),
        "raw_response": response
    }

# ---------- CDEK ----------
def quote_cdek():
    cid=os.environ.get("CDEK_CLIENT_ID","").strip()
    sec=os.environ.get("CDEK_CLIENT_SECRET","").strip()
    if not cid or not sec:
        raise RuntimeError("CDEK credentials missing")
    token_body = urllib.parse.urlencode({
        "grant_type":"client_credentials","client_id":cid,"client_secret":sec
    }).encode()
    _, tok = http_json(
        "https://api.cdek.ru/v2/oauth/token",
        method="POST",
        headers={"Content-Type":"application/x-www-form-urlencoded"},
        data=token_body
    )
    token=tok.get("access_token")
    if not token:
        raise RuntimeError("CDEK OAuth returned no access_token")
    ah={"Authorization":"Bearer "+token}
    def city_code(name):
        url="https://api.cdek.ru/v2/location/cities?"+urllib.parse.urlencode({
            "city":name,"country_codes":"RU","size":100
        })
        _, arr=http_json(url,headers=ah)
        exact=[x for x in arr if str(x.get("city","")).casefold()==name.casefold()]
        x=(exact or arr)[0]
        return int(x["code"])
    from_code=city_code(req["from_city"])
    to_code=city_code(req["to_city"])
    body={
        "type":1,
        "from_location":{"code":from_code},
        "to_location":{"code":to_code},
        "packages":[{
            "weight":int(round(float(req["weight_kg"])*1000)),
            "length":int(req["length_cm"]),
            "width":int(req["width_cm"]),
            "height":int(req["height_cm"])
        }]
    }
    _, ans=http_json(
        "https://api.cdek.ru/v2/calculator/tarifflist",
        method="POST",headers=ah,data=body
    )
    tariffs=ans.get("tariff_codes") or []
    simple=[]
    for t in tariffs:
        simple.append({
            "tariff_code":t.get("tariff_code"),
            "tariff_name":t.get("tariff_name"),
            "delivery_mode":t.get("delivery_mode"),
            "delivery_sum":t.get("delivery_sum"),
            "period_min":t.get("period_min"),
            "period_max":t.get("period_max"),
        })
    door=[t for t in simple if t.get("delivery_mode")==1 and t.get("delivery_sum") is not None]
    avail=door or [t for t in simple if t.get("delivery_sum") is not None]
    best=min(avail,key=lambda x:float(x["delivery_sum"])) if avail else None
    return {
        "status":"УСПЕШНО",
        "from_code":from_code,"to_code":to_code,
        "best_door_to_door_or_lowest":best,
        "tariffs":simple
    }

# ---------- Dalli ----------
def quote_dalli():
    token=os.environ.get("DALLI_TOKEN","").strip()
    if not token:
        raise RuntimeError("DALLI_TOKEN secret missing")

    from_city=str(req.get("from_city","")).strip().casefold()
    if "санкт-петербург" not in from_city and from_city not in ("спб","saint petersburg","st petersburg"):
        return {
            "status":"НЕ_ПРИМЕНИМО",
            "reason":"Подключен Санкт-Петербургский аккаунт Dalli; расчет выполняется для отправлений из Санкт-Петербурга."
        }

    root=ET.Element("deliverycost")
    ET.SubElement(root,"auth",{"token":token})
    ET.SubElement(root,"partner").text="DS"
    ET.SubElement(root,"to").text=req["to_address"]

    sender_code=str(req.get("dalli_sender_code","")).strip()
    if sender_code:
        ET.SubElement(root,"sendercode").text=sender_code

    declared=float(req.get("declared_value_rub",0) or 0)
    ET.SubElement(root,"price").text="0"
    ET.SubElement(root,"inshprice").text=f"{declared:.2f}"
    ET.SubElement(root,"cashservices").text="NO"

    packages=ET.SubElement(root,"packages")
    places=max(1,int(req.get("places",1)))
    for _ in range(places):
        ET.SubElement(packages,"package",{
            "weight":f"{float(req['weight_kg']):g}",
            "length":f"{float(req['length_cm']):g}",
            "width":f"{float(req['width_cm']):g}",
            "height":f"{float(req['height_cm']):g}",
        })
    ET.SubElement(root,"output").text="x2"

    body=ET.tostring(root,encoding="utf-8",xml_declaration=True)
    base=os.environ.get("DALLI_API_BASE_URL","https://spbapi.dalli-service.com/v1").rstrip("/")
    request=urllib.request.Request(
        base+"/",
        data=body,
        method="POST",
        headers={
            "Content-Type":"application/xml; charset=utf-8",
            "Accept":"application/xml, text/xml, */*",
            "User-Agent":"megapolis-shipping-quote/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request,timeout=60) as resp:
            raw=resp.read()
    except urllib.error.HTTPError as e:
        raw=e.read().decode("utf-8","replace")
        raise RuntimeError(f"Dalli HTTP {e.code}: {raw[:1000]}") from e

    parsed=ET.fromstring(raw)
    if parsed.get("error"):
        raise RuntimeError(
            f"Dalli API error={parsed.get('error')}: {parsed.get('errormsg','неизвестная ошибка')}"
        )

    prices=[]
    for node in parsed.findall("price"):
        item=dict(node.attrib)
        if item.get("price") is not None:
            try: item["price"]=float(item["price"])
            except Exception: pass
        if item.get("delivery_period") is not None:
            try: item["delivery_period"]=int(item["delivery_period"])
            except Exception: pass
        prices.append(item)

    if not prices and parsed.get("price") is not None:
        item=dict(parsed.attrib)
        try: item["price"]=float(item["price"])
        except Exception: pass
        prices=[item]

    courier=[p for p in prices if p.get("typedelivery")=="KUR" and isinstance(p.get("price"),(int,float))]
    paid=courier or [p for p in prices if isinstance(p.get("price"),(int,float))]
    best=min(paid,key=lambda x:float(x["price"])) if paid else None
    if not best:
        raise RuntimeError("Dalli не вернул доступный тариф: "+ET.tostring(parsed,encoding="unicode")[:1500])

    return {
        "status":"УСПЕШНО",
        "source":"Dalli SPB API v1",
        "best_courier_or_lowest":best,
        "total_rub":round(float(best["price"]),2),
        "tariffs":prices,
        "sender_code_used":sender_code or None
    }

# ---------- PEK private API ----------
def quote_pek():
    login=os.environ.get("PEK_LOGIN","").strip()
    key=os.environ.get("PEK_API_KEY","").strip()
    if not login or not key:
        raise RuntimeError("PEK credentials missing")
    auth="Basic "+base64.b64encode((login+":"+key).encode()).decode()
    headers={"Authorization":auth,"Content-Type":"application/json; charset=utf-8"}
    base="https://kabinet.pecom.ru/api/v1"
    _, zone=http_json(base+"/branches/findzonebyaddress/",method="POST",headers=headers,data={"address":req["to_address"]})
    wid=zone.get("mainWarehouseId") if isinstance(zone,dict) else None
    if not wid:
        raise RuntimeError("PEK receiver warehouse not found: "+jdump(zone)[:1000])
    planned=(datetime.now(timezone.utc)+timedelta(days=1)).strftime("%Y-%m-%dT10:00:00")
    vol=(float(req["length_cm"])*float(req["width_cm"])*float(req["height_cm"]))/1_000_000
    cargo={
        "length":float(req["length_cm"])/100,
        "width":float(req["width_cm"])/100,
        "height":float(req["height_cm"])/100,
        "volume":round(vol,6),
        "isHP":False,
        "sealingPositionsCount":0,
        "weight":float(req["weight_kg"])
    }
    body={
        "currencyCode":"643",
        "plannedDateTime":planned,
        "types":[3],
        "receiverWarehouseId":wid,
        "isOpenCarSender":False,
        "isOpenCarReceiver":False,
        "isHyperMarket":False,
        "isInsurance":False,
        "isPickUp":True,
        "isDelivery":True,
        "needReturnDocuments":False,
        "needArrangeTransportationDocuments":False,
        "pickupServices":{"isLoading":False,"floor":0,"carryingDistance":0,"isElevator":False},
        "deliveryServices":{"isLoading":False,"floor":0,"carryingDistance":0,"isElevator":False},
        "pickup":{"address":req["from_address"]},
        "delivery":{"address":req["to_address"]},
        "cargos":[cargo for _ in range(int(req.get("places",1)))]
    }
    _, ans=http_json(base+"/calculator/calculateprice/",method="POST",headers=headers,data=body)
    transfers=ans.get("transfers") or []
    if not transfers:
        raise RuntimeError("PEK no transfers: "+jdump(ans)[:1500])
    tr=transfers[0]
    services=[]
    for s in tr.get("services") or []:
        services.append({
            "serviceType":s.get("serviceType"),
            "info":s.get("info"),
            "cost":s.get("cost"),
        })
    total = tr.get("costTotal")
    if total is None or float(total) <= 0:
        # PEK private API can return 0 for intra-branch Moscow routes.
        # Fall back to the public calculator used on pecom.ru.
        _, towns = http_json("https://pecom.ru/ru/calc/towns.php")
        def city_id(name):
            hits=[]
            if isinstance(towns,dict):
                for region,cities in towns.items():
                    if isinstance(cities,dict):
                        for cid,cname in cities.items():
                            if str(cname).strip().casefold()==name.casefold():
                                hits.append(str(cid))
            if not hits:
                raise RuntimeError("PEK public calculator city not found: "+name)
            return hits[0]
        from_id=city_id(req["from_city"])
        to_id=city_id(req["to_city"])
        params=[]
        vals=[
            float(req["width_cm"])/100,
            float(req["height_cm"])/100,
            float(req["length_cm"])/100,
            round(vol,6),
            float(req["weight_kg"]),
            0,0
        ]
        for v in vals:
            params.append(("places[0][]",v))
        params += [
            ("take[town]",from_id),
            ("take[moscow]",0),
            ("deliver[town]",to_id),
            ("deliver[moscow]",0),
            ("strah",0),
            ("pal",0)
        ]
        url="https://calc.pecom.ru/bitrix/components/pecom/calc/ajax.php?"+urllib.parse.urlencode(params)
        _, pub=http_json(url)
        def part(x):
            if isinstance(x,(list,tuple)) and len(x)>=3:
                try: return float(x[2])
                except Exception: return 0.0
            return 0.0
        take=part(pub.get("take"))
        deliver=part(pub.get("deliver"))
        auto=part(pub.get("autonegabarit")) or part(pub.get("auto"))
        add=sum(part(pub.get(k)) for k in ("ADD","ADD_1","ADD_2","ADD_3","ADD_4"))
        public_total=take+deliver+auto+add
        if public_total <= 0:
            return {
                "status":"НЕТ_ТАРИФА",
                "total_rub":0,
                "error":"PEK private and public calculators did not return a valid paid quote.",
                "branch_sender":ans.get("branchSender"),
                "branch_receiver":ans.get("branchReceiver")
            }
        return {
            "status":"УСПЕШНО",
            "source":"public_calculator_fallback",
            "pickup_rub":round(take,2),
            "linehaul_rub":round(auto,2),
            "delivery_rub":round(deliver,2),
            "additional_rub":round(add,2),
            "total_rub":round(public_total,2),
            "est_delivery_time_days":None,
            "services":services,
            "branch_sender":ans.get("branchSender"),
            "branch_receiver":ans.get("branchReceiver")
        }
    return {
        "status":"УСПЕШНО",
        "source":"private_api",
        "total_rub":total,
        "est_delivery_time_days":tr.get("estDeliveryTime"),
        "services":services,
        "branch_sender":ans.get("branchSender"),
        "branch_receiver":ans.get("branchReceiver")
    }

safe("e_bulky", quote_ebulky)
safe("cdek", quote_cdek)
safe("dalli", quote_dalli)
safe("pek", quote_pek)

# comparison
offers=[]
eb=result["carriers"].get("e_bulky",{})
if eb.get("status")=="УСПЕШНО" and eb.get("total_rub") is not None:
    offers.append({"carrier":"e-bulky","total_rub":float(eb["total_rub"])})
cd=result["carriers"].get("cdek",{})
best=cd.get("best_door_to_door_or_lowest") if isinstance(cd,dict) else None
if cd.get("status")=="УСПЕШНО" and best and best.get("delivery_sum") is not None:
    offers.append({"carrier":"CDEK","total_rub":float(best["delivery_sum"])})
dl=result["carriers"].get("dalli",{})
if dl.get("status")=="УСПЕШНО" and dl.get("total_rub") is not None:
    offers.append({"carrier":"Dalli","total_rub":float(dl["total_rub"])})
pk=result["carriers"].get("pek",{})
if pk.get("status")=="УСПЕШНО" and pk.get("total_rub") is not None:
    offers.append({"carrier":"PEK","total_rub":float(pk["total_rub"])})
offers.sort(key=lambda x:x["total_rub"])
result["comparison"]=offers
result["cheapest"]=offers[0] if offers else None

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with open(OUT_PATH,"w",encoding="utf-8") as f:
    json.dump(result,f,ensure_ascii=False,indent=2)
print(json.dumps(result,ensure_ascii=False,indent=2))
