#!/usr/bin/env python3
from __future__ import annotations
import json, os, requests
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"catalog"/"ozon_office_chair_dictionary_probe.json"
DC=79164512
TYPE=95041
ATTRS={
  8229:"Тип",85:"Бренд",10096:"Цвет товара",10400:"Гарантия",21909:"Материал обивки",
  11260:"Фиксация спинки",6673:"Комната",6650:"Назначение (помещение)",6655:"Рисунок на обивке",
  10344:"Механизм качания",6651:"Исполнение",10596:"Форма поставки",22232:"ТН ВЭД коды ЕАЭС",
  6643:"Материал наполнителя",11256:"Регулировка",22270:"Вид выпуска товара",4389:"Страна-изготовитель",
  10036:"Тип механизма качания",9546:"Стиль дизайна",10131:"Особенности",6656:"Материал корпуса",
  6657:"Покрытие корпуса",23276:"Особенности конструкции и дизайна",10035:"Материал крестовины"
}
headers={"Client-Id":os.environ["OZON_CLIENT_ID"],"Api-Key":os.environ["OZON_API_KEY"],"Content-Type":"application/json","Accept":"application/json"}
sess=requests.Session()
out={}
for aid,name in ATTRS.items():
    body={"description_category_id":DC,"type_id":TYPE,"attribute_id":aid,"language":"DEFAULT","last_value_id":0,"limit":5000}
    r=sess.post("https://api-seller.ozon.ru/v1/description-category/attribute/values",headers=headers,json=body,timeout=120)
    row={"status":r.status_code,"name":name}
    try: data=r.json()
    except: data={}
    vals=data.get("result") or data.get("values") or []
    if isinstance(vals,dict): vals=vals.get("values") or vals.get("items") or []
    # Keep all for modest dictionaries; for huge dictionaries keep likely relevant values.
    simplified=[]
    for v in vals if isinstance(vals,list) else []:
        if not isinstance(v,dict): continue
        simplified.append({"id":v.get("id") or v.get("value_id"),"value":v.get("value") or v.get("name") or v.get("title"),"info":v.get("info")})
    if len(simplified)>500:
        keys=("norden","офис","крес","стул","китай","1 год","12 мес","ткан","сет","металл","алюмин","пласт","нейлон","ппу","синх","пиаст","черн","бирюз","голуб","сереб","9401","регулиров","подголов","подлок","колес","газлифт","соврем","лофт","минимал")
        simplified=[v for v in simplified if any(k in str(v.get("value") or "").casefold() for k in keys)]
    row["values"]=simplified
    row["count"]=len(vals) if isinstance(vals,list) else 0
    if r.status_code>=400: row["error"]=r.text[:1000]
    out[str(aid)]=row
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({k:{"name":v["name"],"status":v["status"],"count":v.get("count"),"kept":len(v.get("values") or [])} for k,v in out.items()},ensure_ascii=False))
