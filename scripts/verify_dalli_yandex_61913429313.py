#!/usr/bin/env python3
import json, os, requests, xml.etree.ElementTree as ET
ORDER="61913429313"; CAMPAIGN="93424614"
Y="https://api.partner.market.yandex.ru"; D="https://api.dalli-service.com/v1/"
def s(v): return str(v or "").strip()
H={"Api-Key":os.environ["YANDEX_MARKET_API_KEY"].strip(),"Accept":"application/json"}
def yg(path):
    r=requests.get(Y+path,headers=H,timeout=60); r.raise_for_status(); d=r.json()
    if isinstance(d,dict) and isinstance(d.get("result"),dict): return d["result"]
    if isinstance(d,dict) and isinstance(d.get("order"),dict): return d["order"]
    return d
o=yg(f"/v2/campaigns/{CAMPAIGN}/orders/{ORDER}")
if isinstance(o,dict) and isinstance(o.get("order"),dict): o=o["order"]
b=yg(f"/v2/campaigns/{CAMPAIGN}/orders/{ORDER}/buyer")
phone=s(b.get("phone")); ext=s(b.get("phoneExtension"))
if ext: phone += f" доб. {ext}"
delivery=o.get("delivery") if isinstance(o.get("delivery"),dict) else {}
expected_note=""
for v in (delivery.get("notes"),o.get("notes"),o.get("buyerNotes"),o.get("comment")):
    if isinstance(v,str) and v.strip(): expected_note=v.strip(); break
root=ET.Element("getbasket"); root.insert(0,ET.Element("auth",{"token":os.environ["DALLI_TOKEN_MSK"].strip()})); ET.SubElement(root,"number").text=ORDER
r=requests.post(D,data=ET.tostring(root,encoding="utf-8",xml_declaration=True),headers={"Content-Type":"application/xml; charset=utf-8"},timeout=60); r.raise_for_status(); x=ET.fromstring(r.content)
order=x.find(".//order")
if order is None: raise RuntimeError("Заказ не найден в корзине")
cl=order.find("./ads/climb")
vats=[s(i.get("VATrate")) for i in order.findall("./items/item")]
res={
 "ok":True,"order_number":ORDER,"account":"МСК","in_basket":True,"sent_to_delivery":False,
 "barcode":s(order.findtext("barcode")),"service":s(order.findtext("service")),
 "date":s(order.findtext("./receiver/date")),"time_min":s(order.findtext("./receiver/time_min")),"time_max":s(order.findtext("./receiver/time_max")),
 "phone_matches_yandex_full":s(order.findtext("./receiver/phone"))==phone,
 "phone_extension_present":bool(ext),
 "instruction_matches_yandex_note":s(order.findtext("instruction"))==expected_note,
 "instruction_empty":s(order.findtext("instruction"))=="",
 "climb_present":cl is not None,"climb_type":s(cl.get("type")) if cl is not None else "",
 "climb_floor":s(cl.get("floor")) if cl is not None else "",
 "vat_values":vats,"vat_all_zero":bool(vats) and all(v=="0" for v in vats),"errors":[]
}
print(json.dumps(res,ensure_ascii=False,indent=2))
if not (res["phone_matches_yandex_full"] and res["instruction_matches_yandex_note"] and res["vat_all_zero"] and res["climb_present"]): raise SystemExit(2)
