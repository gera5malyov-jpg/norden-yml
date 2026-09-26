#!/usr/bin/env python3
from __future__ import annotations
import json, os, re, unicodedata, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import gspread, requests
from google.oauth2.service_account import Credentials

SID=os.environ["CATALOG_SPREADSHEET_ID"].strip()
SHEET=os.environ.get("CATALOG_SHEET","Норден").strip()
SA=os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
NORDEN_SECRET=os.environ.get("NORDEN_SECRET","").strip()
FULL_XML="https://norden.group/index.php?dispatch=sw_user_prices.get_file&file=Norden.xml"
PRICE_XML="https://norden.group/index.php?dispatch=sw_user_prices.get_file&file=Norden.group+-K8%25.xml"
OUT=Path("catalog/norden_table_only_append_report.json")
APPROVAL="Проверено — загрузить в KIT/Webasyst"
MARKET_APPROVAL="Проверено — загрузить в Ozon и Яндекс.Маркет"

def s(v): return str(v or "").strip()
def ntext(v): return re.sub(r"\s+"," ",unicodedata.normalize("NFKC",s(v)).casefold()).strip()
def num(v):
    x=s(v).replace("\xa0"," ").replace(" ","").replace(",",".")
    if not x: return 0.0
    try: return float(x)
    except: return 0.0
def get(url):
    r=requests.get(url,headers={"User-Agent":"Mozilla/5.0"},timeout=180)
    r.raise_for_status()
    return r.content
def is_target(name,group):
    nn=ntext(name).replace("ё","е")
    gg=ntext(group).replace("ё","е")
    # Only actual chairs/stools: the product name itself must identify a chair/stool.
    if "кресл" not in nn and "стул" not in nn:
        return False
    if "уценк" in nn or "уценк" in gg:
        return False
    excluded=("чехол","сменный чехол","подголовник","подлокотник","крестовина","газлифт","ролик","колеса","колесо","механизм","сиденье","спинка")
    if any(x in nn for x in excluded):
        return False
    return True

full=ET.fromstring(get(FULL_XML))
price=ET.fromstring(get(PRICE_XML))

price_idx={}
for x in price.iter("Номенклатура"):
    a=s(x.findtext("Артикул"))
    if not a: continue
    prices={s(p.attrib.get("ВидЦен")):num(p.text) for p in x.findall("Цена")}
    stocks={s(st.attrib.get("Склад")):num(st.text) for st in x.findall("СвободныйОстаток")}
    price_idx[a]={
      "purchase":prices.get("Опт",0.0),
      "rrp":prices.get("РРЦ",0.0),
      "msk":stocks.get("Основной склад",0.0),
      "spb":stocks.get("Питер Основной склад",0.0),
    }
    price_idx[a]["total"]=price_idx[a]["msk"]+price_idx[a]["spb"]

items=[]
for x in full.iter("Номенклатура"):
    a=s(x.findtext("Артикул"))
    if not a: continue
    name=s(x.findtext("НаименованиеПолное")) or s(x.findtext("Наименование")) or a
    group=s(x.findtext("Группа"))
    if not is_target(name,group): continue
    p=price_idx.get(a) or {}
    if float(p.get("total") or 0)<=0: continue
    images=[]
    for c in list(x):
        if s(c.tag).startswith("Ссылканафото") and s(c.text):
            images.append(s(c.text))
    items.append({
      "yml":a,
      "name":name,
      "group":group,
      "norden_code":s(x.findtext("Код")),
      "purchase":p.get("purchase",0),
      "rrp":p.get("rrp",0),
      "msk":p.get("msk",0),
      "spb":p.get("spb",0),
      "total":p.get("total",0),
      "images":list(dict.fromkeys(images)),
    })

creds=json.loads(SA)
gc=gspread.authorize(Credentials.from_service_account_info(
    creds,scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
))
ws=gc.open_by_key(SID).worksheet(SHEET)
vals=ws.get_all_values()
headers=list(vals[0])
idx={h:i for i,h in enumerate(headers)}
required=["Название","YML ID","Бренд","Источник","Тип Webasyst при загрузке","Закупка","РРЦ поставщика","Остаток",APPROVAL,MARKET_APPROVAL]
missing=[h for h in required if h not in idx]
if missing: raise RuntimeError("Missing columns: "+", ".join(missing))

# Exact literal supplier YML ID only. If anything differs, treat as a separate new product.
existing={s(r[idx["YML ID"]]) for r in vals[1:] if idx["YML ID"]<len(r) and s(r[idx["YML ID"]])}
new=[x for x in items if x["yml"] not in existing]
new.sort(key=lambda x:(x["name"],x["yml"]))

def supplier_export_images(yml_id, xml_images):
    # Only official Norden supplier exports are allowed as the image source.
    # Prefer the official API export because it can contain corrected image paths;
    # XML/YML export remains a supplier-export fallback. Never scrape the website or use third-party catalogs.
    if NORDEN_SECRET:
        try:
            r=requests.get(
                "https://norden.group/api-products/",
                headers={"secret":NORDEN_SECRET,"Accept":"application/json"},
                params={"sku":yml_id},
                timeout=120,
            )
            r.raise_for_status()
            rows=(r.json() or {}).get("products") or []
            exact=[p for p in rows if s(p.get("product_code"))==yml_id]
            if len(exact)==1:
                api_images=list(dict.fromkeys([s(u) for u in (exact[0].get("images") or []) if s(u)]))
                if api_images:
                    return api_images,"api"
        except Exception:
            pass
    return list(dict.fromkeys([s(u) for u in (xml_images or []) if s(u)])),"xml"

# Ignore checkbox-only/formatted blank rows. Append after the last real product row.
article_i=idx.get("Артикул")
name_i=idx.get("Название")
yml_i=idx["YML ID"]
last_data_row=1
for rn,r in enumerate(vals[1:],start=2):
    keys=[]
    for ci in (article_i,name_i,yml_i):
        if ci is not None and ci < len(r):
            keys.append(s(r[ci]))
    if any(keys):
        last_data_row=rn
start=last_data_row+1
rows=[]
for x in new:
    row=[""]*len(headers)
    def put(col,val):
        if col in idx: row[idx[col]]=val
    put("Название",x["name"])
    put("YML ID",x["yml"])
    put("Бренд","Norden")
    put("Источник","Norden")
    put("Тип Webasyst при загрузке","NORDEN-100")
    put("Закупка",x["purchase"] if x["purchase"] else "")
    put("РРЦ поставщика",x["rrp"] if x["rrp"] else "")
    put("Остаток",x["total"])
    put("YML ID Norden",x["yml"])
    put("Код Norden",x["norden_code"])
    put("Статус источника","НОВЫЙ — ТОЛЬКО ТАБЛИЦА")
    put("Остаток МСК Norden",x["msk"])
    put("Остаток СПБ Norden",x["spb"])
    put("Остаток всего",x["total"])
    put("Дата источника",datetime.now(timezone.utc).isoformat())
    put(APPROVAL,False)
    put(MARKET_APPROVAL,False)
    images,image_source=supplier_export_images(x["yml"],x.get("images") or [])
    if images:
        put("Основное фото",'=IMAGE("'+images[0].replace('"','""')+'")')
        put("Фото",json.dumps(images,ensure_ascii=False))
    rows.append(row)

if rows:
    # Ensure the grid is large enough, then write only to the Google Sheet.
    need=start+len(rows)-1
    if ws.row_count < need:
        ws.resize(rows=need+20)
    # No marketplace/KIT/Webasyst client exists in this script.
    ws.update(
      range_name=f"A{start}:{gspread.utils.rowcol_to_a1(start+len(rows)-1,len(headers))}",
      values=rows,
      value_input_option="USER_ENTERED"
    )
    # Ensure all three approval columns render as native checkboxes on newly appended rows.
    for col in (APPROVAL,MARKET_APPROVAL):
        ci=idx[col]
        ws.spreadsheet.batch_update({"requests":[{
          "setDataValidation":{
            "range":{"sheetId":ws.id,"startRowIndex":start-1,"endRowIndex":start-1+len(rows),"startColumnIndex":ci,"endColumnIndex":ci+1},
            "rule":{"condition":{"type":"BOOLEAN"},"strict":True,"showCustomUi":True}
          }
        }]})

report={
  "ok":True,
  "mode":"TABLE_ONLY",
  "rules":{
    "scope":"кресла и стулья",
    "stock":"> 0 (МСК + СПБ Norden)",
    "markdown":"исключать товары, где в названии/группе есть 'уценк'",
    "identity":"только точное буквальное совпадение YML ID означает существующий товар; любые отличия считаются новым товаром",
    "external_uploads":"запрещены",
    "images":"только официальные выгрузки Norden (API/XML/YML); сайт вручную и сторонние источники запрещены"
  },
  "eligible_supplier_items":len(items),
  "existing_exact_yml_count":len(existing),
  "added_count":len(new),
  "last_data_row_before":last_data_row,
  "start_row":start if new else None,
  "end_row":start+len(new)-1 if new else None,
  "added":[{"yml_id":x["yml"],"name":x["name"],"stock":x["total"],"purchase":x["purchase"],"rrp":x["rrp"]} for x in new]
}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(report,ensure_ascii=False,indent=2))
