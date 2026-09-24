#!/usr/bin/env python3
import json, os, requests, xml.etree.ElementTree as ET

ORDER_NO = "34598568-0351-1"
OZON_BASE = "https://api-seller.ozon.ru"
DALLI_BASE = "https://spbapi.dalli-service.com/v1/"
OUT = "dalli/order_34598568-0351-1_spb_delivery_quote.json"

def s(v): return str(v or "").strip()

def ozon_post(path, body):
    r = requests.post(
        OZON_BASE + path,
        headers={
            "Client-Id": s(os.environ["OZON_CLIENT_ID"]),
            "Api-Key": s(os.environ["OZON_API_KEY"]),
            "Content-Type": "application/json",
        },
        json=body, timeout=60
    )
    if not r.ok:
        raise RuntimeError(f"Ozon {path} HTTP {r.status_code}: {r.text[:500]}")
    return r.json()

def dalli_post(root):
    root.insert(0, ET.Element("auth", {"token": s(os.environ["DALLI_TOKEN"])}))
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    r = requests.post(DALLI_BASE, data=payload, headers={"Content-Type":"application/xml; charset=utf-8"}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Dalli HTTP {r.status_code}: {r.text[:500]}")
    return ET.fromstring(r.content)

def unit_cm(v, unit):
    x=float(v)
    u=s(unit).lower()
    if u in ("mm","миллиметр","миллиметры"): return x/10
    if u in ("m","метр","метры"): return x*100
    return x

def unit_kg(v, unit):
    x=float(v)
    u=s(unit).lower()
    if u in ("g","гр","г","gram","grams"): return x/1000
    return x

order = ozon_post("/v3/posting/fbs/get", {
    "posting_number": ORDER_NO,
    "with":{"analytics_data":False,"barcodes":False,"financial_data":False,"translit":False}
})
order = order.get("result", order)
cust = order.get("customer") or {}
addr = cust.get("address") or {}
parts=[]
for k in ("zip_code","country","region","city","address_tail"):
    v=s(addr.get(k))
    if v and v not in parts: parts.append(v)
address=", ".join(parts)
products=[p for p in (order.get("products") or []) if isinstance(p,dict)]
offer=s(products[0].get("offer_id")) if products else ""
declared=sum(float(p.get("price") or 0)*max(1,int(p.get("quantity") or 1)) for p in products)

dims = None
attrs_error = None
try:
    info = ozon_post("/v4/product/info/attributes", {
        "filter":{"offer_id":[offer],"visibility":"ALL"},
        "limit":100,
        "sort_dir":"ASC"
    })
    rows = info.get("result") or info.get("items") or []
    if isinstance(rows, dict):
        rows = rows.get("items") or []
    row = rows[0] if rows else {}
    if row:
        dims = {
            "length_cm": unit_cm(row.get("depth"), row.get("dimension_unit")),
            "width_cm": unit_cm(row.get("width"), row.get("dimension_unit")),
            "height_cm": unit_cm(row.get("height"), row.get("dimension_unit")),
            "weight_kg": unit_kg(row.get("weight"), row.get("weight_unit")),
            "source":"Ozon /v4/product/info/attributes"
        }
except Exception as e:
    attrs_error = str(e)

if not dims or min(dims["length_cm"],dims["width_cm"],dims["height_cm"],dims["weight_kg"]) <= 0:
    dims = {"length_cm":50.0,"width_cm":50.0,"height_cm":100.0,"weight_kg":31.0,"source":"fallback user chair packaging"}

root=ET.Element("deliverycost")
ET.SubElement(root,"partner").text="DS"
ET.SubElement(root,"to").text=address
ET.SubElement(root,"price").text="0"
ET.SubElement(root,"inshprice").text=f"{declared:.2f}"
ET.SubElement(root,"cashservices").text="NO"
pnode=ET.SubElement(root,"packages")
ET.SubElement(pnode,"package",{
    "weight":f'{dims["weight_kg"]:g}',
    "length":f'{dims["length_cm"]:g}',
    "width":f'{dims["width_cm"]:g}',
    "height":f'{dims["height_cm"]:g}',
})
ET.SubElement(root,"output").text="x2"
resp=dalli_post(root)

prices=[]
for node in resp.findall(".//price"):
    prices.append(dict(node.attrib))

result={
    "ok":True,
    "order_number":ORDER_NO,
    "offer_id":offer,
    "address":address,
    "declared_value":round(declared,2),
    "package":dims,
    "attribute_probe_error":attrs_error,
    "dalli_root_attributes":dict(resp.attrib),
    "prices":prices,
}
os.makedirs("dalli",exist_ok=True)
with open(OUT,"w",encoding="utf-8") as f:
    json.dump(result,f,ensure_ascii=False,indent=2); f.write("\n")
print(json.dumps(result,ensure_ascii=False,indent=2))
