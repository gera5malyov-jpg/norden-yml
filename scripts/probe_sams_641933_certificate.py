import json, os, urllib.request, urllib.error, urllib.parse

OFFER="SAMS-641933"
DOC="ЕАЭС N RU Д-CN.РА06.В.58347/26"
OZON="https://api-seller.ozon.ru"
YANDEX="https://api.partner.market.yandex.ru"

oz_headers={
    "Client-Id": os.environ["OZON_CLIENT_ID"],
    "Api-Key": os.environ["OZON_API_KEY"],
    "Content-Type":"application/json",
}
ya_headers={
    "Api-Key": os.environ["YANDEX_MARKET_API_KEY"],
    "Content-Type":"application/json",
    "Accept":"application/json",
}

def call(method,url,headers,body=None):
    data=None if body is None else json.dumps(body,ensure_ascii=False).encode()
    req=urllib.request.Request(url,data=data,headers=headers,method=method)
    try:
        with urllib.request.urlopen(req,timeout=45) as r:
            raw=r.read().decode("utf-8","replace")
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw=e.read().decode("utf-8","replace")
        try: parsed=json.loads(raw) if raw else {}
        except Exception: parsed={"raw":raw[:4000]}
        return e.code, parsed

st,oz=call("POST",OZON+"/v3/product/list",oz_headers,{
    "filter":{"offer_id":[OFFER],"visibility":"ALL"},"limit":100
})
print("OZON_PRODUCT_LIST_STATUS",st)
print("OZON_PRODUCT_LIST",json.dumps(oz,ensure_ascii=False)[:8000])
items=((oz.get("result") or {}).get("items") or [])
target=next((x for x in items if str(x.get("offer_id") or "")==OFFER),None)
if not target:
    raise SystemExit("Ozon target offer not found")
print("OZON_TARGET",json.dumps(target,ensure_ascii=False))

st,info=call("POST",OZON+"/v3/product/info/list",oz_headers,{"offer_id":[OFFER]})
print("OZON_INFO_STATUS",st)
print("OZON_INFO",json.dumps(info,ensure_ascii=False)[:10000])

for label,path,body in [
    ("OPTIONS_EMPTY","/v2/product/certification/options",{}),
    ("PARAMS_TYPE_ONLY","/v2/product/certification/params",{"params":{
        "certificate_type":"DECLARATION"
    }}),
    ("PARAMS_CORE","/v2/product/certification/params",{"params":{
        "name":"Декларация о соответствии",
        "number":DOC,
        "issue_date":"2026-07-30T00:00:00Z",
        "expired_date":{"date":{"day":28,"month":7,"year":2031}},
        "certificate_type":"DECLARATION"
    }})
]:
    st,res=call("POST",OZON+path,oz_headers,body)
    print("OZON_META",label,path,"STATUS",st)
    print(json.dumps(res,ensure_ascii=False)[:30000])

st,res=call("POST",OZON+"/v1/product/certificate/list",oz_headers,{
    "offer_id":OFFER,"page":1,"page_size":100
})
print("OZON_CERT_LIST_STATUS",st)
print("OZON_CERT_LIST",json.dumps(res,ensure_ascii=False)[:15000])

st,res=call("POST",OZON+"/v1/product/certificate/info",oz_headers,{
    "certificate_number":DOC
})
print("OZON_CERT_INFO_STATUS",st)
print("OZON_CERT_INFO",json.dumps(res,ensure_ascii=False)[:15000])

st,camps=call("GET",YANDEX+"/v2/campaigns?limit=100",ya_headers)
print("YANDEX_CAMPAIGNS_STATUS",st)
print("YANDEX_CAMPAIGNS",json.dumps(camps,ensure_ascii=False)[:30000])

bid="20806099"
token=None
found=None
for _ in range(100):
    qs={"limit":100}
    if token: qs["pageToken"]=token
    url=f"{YANDEX}/v2/businesses/{bid}/offer-mappings?"+urllib.parse.urlencode(qs)
    st,res=call("POST",url,ya_headers,{})
    if st != 200:
        print("YANDEX_OFFER_STATUS",st)
        print("YANDEX_OFFER_ERROR",json.dumps(res,ensure_ascii=False)[:8000])
        break
    result=res.get("result") or {}
    for row in result.get("offerMappings") or []:
        off=row.get("offer") or {}
        if str(off.get("offerId") or "")==OFFER:
            found=row
            break
    if found: break
    nt=str(((result.get("paging") or {}).get("nextPageToken")) or "")
    if not nt or nt==token: break
    token=nt

print("YANDEX_TARGET",json.dumps(found,ensure_ascii=False)[:15000])
if not found:
    print("YANDEX_TARGET_NOT_FOUND")
