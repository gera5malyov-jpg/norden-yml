#!/usr/bin/env python3
import json, os, time, urllib.request, urllib.error
from collections import Counter, defaultdict

BASE="https://api-seller.ozon.ru"
CID=os.environ["OZON_CLIENT_ID"].strip()
KEY=os.environ["OZON_API_KEY"].strip()
H={"Client-Id":CID,"Api-Key":KEY,"Content-Type":"application/json","Accept":"application/json","User-Agent":"megapolis-certificate-audit/1.0"}

def req(method,path,payload=None,attempts=5):
    data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode()
    for a in range(attempts):
        r=urllib.request.Request(BASE+path,data=data,headers=H,method=method)
        try:
            with urllib.request.urlopen(r,timeout=120) as f:
                raw=f.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            body=e.read().decode("utf-8","replace")
            if (e.code==429 or 500<=e.code<600) and a+1<attempts:
                time.sleep(min(2**a,20)); continue
            return {"__http_error__":e.code,"__body__":body[:3000]}
        except Exception as e:
            if a+1<attempts:
                time.sleep(min(2**a,20)); continue
            return {"__error__":repr(e)}

def post(path,payload): return req("POST",path,payload)
def get(path): return req("GET",path)

def chunks(xs,n):
    for i in range(0,len(xs),n): yield xs[i:i+n]

def list_products():
    out=[]; last=""; seen=set()
    while True:
        body={"filter":{"visibility":"ALL"},"limit":1000}
        if last: body["last_id"]=last
        d=post("/v3/product/list",body)
        r=d.get("result") or {}
        items=r.get("items") or []
        out+=items
        nxt=r.get("last_id") or ""
        total=int(r.get("total") or 0)
        if not items or (total and len(out)>=total) or not nxt or nxt==last or nxt in seen: break
        seen.add(last); last=nxt
    return out

def attrs(ids):
    out=[]
    for b in chunks(ids,1000):
        d=post("/v4/product/info/attributes",{"filter":{"product_id":b,"visibility":"ALL"},"limit":1000})
        out += d.get("result") or []
    return out

def brand(p):
    for a in p.get("attributes") or []:
        if int(a.get("id") or a.get("attribute_id") or 0)==85:
            return " | ".join(str(v.get("value") or "").strip() for v in a.get("values") or [] if str(v.get("value") or "").strip())
    return ""

def list_certs():
    out=[]; errors=[]
    for page in range(1,101):
        d=post("/v1/product/certificate/list",{"page":page,"page_size":100})
        if "__http_error__" in d or "__error__" in d:
            errors.append(d); break
        rows=d.get("result")
        if isinstance(rows,dict):
            items=rows.get("items") or rows.get("certificates") or rows.get("result") or []
            total=int(rows.get("total") or 0)
        else:
            items=rows or d.get("items") or d.get("certificates") or []
            total=int(d.get("total") or 0)
        if not isinstance(items,list): items=[]
        out+=items
        if not items or len(items)<1000 or (total and len(out)>=total): break
    return out,errors

aliases={
 "Бремен":["БРЕМЕН","BREMEN"],
 "БТС":["БТС","МК БТС","BTS"],
 "Интерьер-центр":["ИНТЕРЬЕР-ЦЕНТР","ИНТЕРЬЕР ЦЕНТР","INTERIOR CENTER"],
 "Мебель-трейд":["МЕБЕЛЬ-ТРЕЙД","МЕБЕЛЬ ТРЕЙД","STATUS"],
 "Мира":["MIRA","МИРА"],
 "Моби":["MOBI","МОБИ"],
 "Модуль":["МОДУЛЬ","MODUL","MODULE"],
 "Орматек":["ОРМАТЕК","ORMATEK"],
 "Стендмебель":["СТЕНДМЕБЕЛЬ","STENDMEBEL"],
 "ТЭКС":["ТЭКС","TEX","TEKS"],
 "Феникс":["ФЕНИКС","FENIX","PHOENIX"],
 "Элегия":["ЭЛЕГИЯ","ELEGIA"],
 "Эра":["ЭРА","ERA"],
}

options=post("/v2/product/certification/options",{})
cert_params=post("/v2/product/certification/params",{"params":{"certificate_type":"DECLARATION"}})\naccord=get("/v2/product/certificate/accordance-types/list")
types=get("/v1/product/certificate/types")
certs,cert_errors=list_certs()
pl=list_products()
ids=[int(x.get("product_id") or 0) for x in pl if int(x.get("product_id") or 0)]
aa=attrs(ids)
by_id={int(x.get("id") or x.get("product_id") or 0):x for x in aa}
brand_counts=Counter()
samples=defaultdict(list)
supplier_hits=defaultdict(list)
for pid,p in by_id.items():
    b=brand(p).strip()
    if b: brand_counts[b]+=1
    offer=str(p.get("offer_id") or "")
    name=str(p.get("name") or "")
    hay=(b+" "+offer+" "+name).upper()
    for sup,als in aliases.items():
        if any(a.upper() in hay for a in als):
            supplier_hits[sup].append({"product_id":pid,"offer_id":offer,"name":name,"brand":b})
    if b and len(samples[b])<3:
        samples[b].append({"offer_id":offer,"name":name})

out={
 "options":options,
 "certification_params_declaration":cert_params,\n "accordance_types":accord,
 "certificate_types":types,
 "certificate_count":len(certs),
 "certificate_errors":cert_errors,
 "certificates":certs[:2000],
 "product_count":len(by_id),
 "brand_counts":dict(brand_counts.most_common()),
 "brand_samples":dict(samples),
 "supplier_hits":{k:{"count":len(v),"sample":v[:20]} for k,v in supplier_hits.items()},
}
print("===CERTIFICATE_AUDIT_JSON===")
open("ozon/certificates/audit_report.json","w",encoding="utf-8").write(json.dumps(out,ensure_ascii=False,indent=2)+"\n")
print(json.dumps({"certificate_count":len(certs),"product_count":len(by_id),"supplier_counts":{k:len(v) for k,v in supplier_hits.items()}},ensure_ascii=False))
