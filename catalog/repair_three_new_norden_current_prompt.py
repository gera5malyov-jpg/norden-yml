#!/usr/bin/env python3
from __future__ import annotations
import hashlib, importlib.util, json, math, os, re, sys
from collections import defaultdict
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
import gspread, requests
from google.oauth2.service_account import Credentials

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"catalog"/"repair_three_new_norden_current_prompt_report.json"
SID="1DvMYvKRr8fX8mfLFaeJCXCccQofPViPcIP2CgIctc8w"
SHEET="Норден"
OFFICE_PATH=["Мебель","Компьютерная и офисная мебель","Кресла офисные и компьютерные","Офисные кресла"]
CHAIR_PATH=["Мебель","Столы и стулья","Стулья"]
OZON_DC=79164512
OZON_TYPE=95041

def load(path,name):
    p=ROOT/path
    spec=importlib.util.spec_from_file_location(name,p)
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

os.environ.setdefault("CATALOG_SPREADSHEET_ID",SID)
os.environ.setdefault("GOOGLE_SERVICE_ACCOUNT_JSON","{}")
SYNC=load(Path("catalog")/"sync_norden_chairs_stools_6h.py","repair3_sync")
MOD=SYNC.MOD; BRIDGE=SYNC.BRIDGE
sys.path.insert(0,str(ROOT/"webasyst"))
from client import WebasystClient

def s(v): return str(v or "").strip()
def norm(v): return re.sub(r"\s+"," ",s(v)).casefold().strip()
def ms(v): return f"{Decimal(str(v)).quantize(Decimal('0.01')):.2f}"
def ceilrub(v): return int(Decimal(str(v)).to_integral_value(rounding=ROUND_CEILING))
def jsons(v): return json.dumps(v,ensure_ascii=False,separators=(",",":"))

TARGETS={
"AF-31662372":{"wa_pid":"1483234","yml":"CK38F","category_path":CHAIR_PATH,"model":"Pinin Smart New","ozon_name":"Офисное кресло Norden Pinin Smart New CK38F, черный","color":"черный","country":"Китай","warranty":"1 год","tnved":"9401710009","hashtags":["#для_переговорной","#для_конференций","#для_обучения","#для_офиса","#зона_ожидания","#рабочее_пространство","#офисный_интерьер","#современный_интерьер","#деловой_интерьер","#учебный_центр","#переговорная_комната","#эргономичный_дизайн"]},
"AF-31662420":{"wa_pid":"1483282","yml":"B1816 3S fabric LE8100-07","category_path":OFFICE_PATH,"model":"Corfu","ozon_name":"Офисное кресло Norden Corfu B1816 3S fabric LE8100-07, бирюзовый","color":"бирюзовый","country":"Китай","warranty":"1 год","tnved":"9401390000","hashtags":["#для_переговорной","#конференц_зал","#для_офиса","#рабочее_пространство","#офисный_интерьер","#современный_интерьер","#деловой_интерьер","#комфортный_офис","#переговорная_комната","#эргономичный_дизайн","#минимализм"]},
"AF-31662421":{"wa_pid":"1483283","yml":"CK-2518A-P","category_path":OFFICE_PATH,"model":"Next Black","ozon_name":"Офисное кресло Norden Next Black CK-2518A-P, черный","color":"черный","country":"Китай","warranty":"1 год","tnved":"9401390000","hashtags":["#эргономичный_офис","#рабочее_место","#для_офиса","#домашний_кабинет","#рабочее_пространство","#офисный_интерьер","#современный_интерьер","#комфортный_офис","#деловой_интерьер","#долгая_работа","#эргономичный_дизайн","#минимализм"]},
}

secret=os.environ["NORDEN_SECRET"]
source={}
for article,cfg in TARGETS.items():
    r=requests.get("https://norden.group/api-products/",headers={"secret":secret,"Accept":"application/json"},params={"sku":cfg["yml"]},timeout=120); r.raise_for_status()
    rows=(r.json() or {}).get("products") or []
    exact=[p for p in rows if s(p.get("product_code"))==cfg["yml"]]
    if len(exact)!=1: raise RuntimeError(f"Norden API exact {cfg['yml']}: {len(exact)}")
    source[article]=exact[0]

ps,_=SYNC.price_stock()
for article,cfg in TARGETS.items():
    p=ps.get(SYNC.nc(cfg["yml"]))
    if not p: raise RuntimeError(f"Norden price feed missing {cfg['yml']}")
    source[article]["_price_stock"]=p

wa=WebasystClient(min_request_interval=0.45)
kit=BRIDGE.KitClient()
warehouses={SYNC.nt(x.get("title") or x.get("name")):s(x.get("id")) for x in kit.warehouses()}
msk_id=warehouses.get(SYNC.nt(SYNC.KIT_MSK)); spb_id=warehouses.get(SYNC.nt(SYNC.KIT_SPB))
if not msk_id or not spb_id: raise RuntimeError("KIT warehouse IDs missing")
cats=kit.categories(); chars=kit.characteristics()
bytitle=defaultdict(list)
for x in chars:
    if s(x.get("title")): bytitle[MOD.norm_title(x["title"])].append(x)
def char_id(title): return MOD.characteristic_id(kit,chars,bytitle,title)
def put_char(existing,title,value):
    if value is None or s(value)=="": return existing
    cid=char_id(title); row={"characteristic_id":cid,"value":s(value),"values":[s(value)]}
    return [x for x in existing if s(x.get("characteristic_id"))!=cid]+[row]
def oz(title,aid): return f"Ozon {aid} — {title}"
def feature_map(p): return {norm(x.get("name")):s(x.get("value")) for x in (p.get("features") or []) if s(x.get("name"))}
def fval(f,*names):
    for n in names:
        v=f.get(norm(n))
        if v!="": return v
    return ""
def numtext(v): return s(v).replace(",",".").strip()

def item_from_source(article,wa_name):
    p=source[article]; cfg=TARGETS[article]; fm=feature_map(p); st=p["_price_stock"]
    desc=s(p.get("description")) or fval(fm,"Особенности модели")
    return {"article":cfg["yml"],"name":wa_name or s(p.get("name")) or cfg["model"],"norden_code":s(p.get("Kod")),"description":desc,
      "images":list(dict.fromkeys([s(x) for x in (p.get("images") or []) if s(x)])),
      "characteristics":[(s(x.get("name")),s(x.get("value"))) for x in (p.get("features") or []) if s(x.get("name")) and s(x.get("value"))],
      "purchase":st.get("purchase"),"rrp":st.get("rrp"),"msk":st.get("msk",0),"spb":st.get("spb",0),"total":st.get("total",0)}

def exact_kit(article):
    d=kit.request("GET","/v1/variants",params={"name":article,"page":1,"per_page":100})
    return [x for x in kit.items(d) if s(x.get("sku"))==article]
def public_urls(variant_id): return SYNC.kit_public_image_urls(kit,variant_id)

def ozon_values(article,p):
    cfg=TARGETS[article]; fm=feature_map(p)
    H=numtext(fval(fm,"Высота см")); W=numtext(fval(fm,"Ширина см")); D=numtext(fval(fm,"Длина см","Глубина кресла"))
    seat_min=numtext(fval(fm,"Высота от пола до сиденья мин")); seat_max=numtext(fval(fm,"Высота от пола до сиденья макс"))
    seat_w=numtext(fval(fm,"Ширина сиденья","Ширина Сиденья")); seat_d=numtext(fval(fm,"Глубина сиденья")); back_h=numtext(fval(fm,"Высота спинки"))
    netkg=numtext(fval(fm,"Вес, кг")); netg=str(int(round(float(netkg)*1000))) if netkg else ""
    frame=fval(fm,"Материал каркаса"); cross=fval(fm,"Материал крестовины"); seat_mat=fval(fm,"Сиденье материал","Сидушка материал"); back_mat=fval(fm,"Спинка материал")
    upholstery=";".join(dict.fromkeys([x for x in (seat_mat,back_mat) if x and x.casefold() not in ("нет","n")]))
    fill=fval(fm,"Сиденье наполнение","Сидушка наполнение"); fill_oz="Без наполнителя" if fill.casefold() in ("нет","n","") else ("Пенополиуретан" if fill.upper()=="ППУ" else fill)
    desc=fval(fm,"Особенности модели") or s(p.get("description")); maxload=fval(fm,"Выдерживает вес")
    if article=="AF-31662372":
        rocking="Нет"; mech_type="Без механизма качания"; regulation="Без регулировки"; features=["Без подголовника","С подлокотниками"]; maxload=""; min_h=max_h=H
    elif article=="AF-31662420":
        rocking="Да"; mech_type="Опора-пиастра"; regulation="Высоты сиденья"; features=["Без подголовника","С подлокотниками","Регулировка по высоте","Механизм качания","На колесиках"]; min_h=numtext(fval(fm,"Высота кресла минимум")); max_h=numtext(fval(fm,"Высота кресла максимум"))
    else:
        rocking="Да"; mech_type="Синхронный"; regulation=";".join(["Высоты подголовника","Высоты подлокотников","Высоты сиденья","Глубины сиденья","Угла наклона подголовника"]); features=["С подголовником","Регулируемый подголовник","С подлокотниками","Регулируемые подлокотники","Регулировка по высоте","Механизм качания","Регулировка по глубине","На колесиках"]; min_h=numtext(fval(fm,"Высота кресла минимум")); max_h=numtext(fval(fm,"Высота кресла максимум"))
    vals={
      oz("description_category_id",0):str(OZON_DC),oz("type_id",0):str(OZON_TYPE),oz("Статус подготовки",0):"ПОДГОТОВЛЕНО В KIT — НЕ ВЫГРУЖЕНО",
      oz("Тип",8229):"Офисное кресло",oz("Название модели (для объединения в одну карточку)",9048):cfg["model"],oz("Название модели для шаблона наименования",12141):cfg["model"],oz("Бренд",85):"Norden",oz("Код продавца",9024):article,oz("Название",4180):cfg["ozon_name"],
      oz("Цвет товара",10096):cfg["color"],oz("Название цвета",10097):cfg["color"],oz("Гарантия",10400):cfg["warranty"],oz("Материал обивки",21909):upholstery,
      oz("Комната",6673):"Офис;Кабинет",oz("Назначение (помещение)",6650):"Для офиса;Для общественных мест"+(";Для школы" if article=="AF-31662372" else ""),oz("Рисунок на обивке",6655):"Однотонный",
      oz("Высота, см",10174):H,oz("Ширина, см",10175):W,oz("Глубина, см",10176):D,oz("Минимальная высота, см",10905):min_h,oz("Максимальная высота, см",10906):max_h,
      oz("Мин. высота сиденья, см",6664):seat_min,oz("Макс. высота сиденья, см",11280):seat_max,oz("Ширина сиденья, см",6665):seat_w,oz("Глубина сиденья, см",6666):seat_d,oz("Высота спинки, см",6667):back_h,
      oz("Макс. нагрузка, кг",7915):maxload,oz("#Хештеги",23171):" ".join(cfg["hashtags"][:30]),oz("Механизм качания",10344):rocking,oz("Тип механизма качания",10036):mech_type,oz("Регулировка",11256):regulation,oz("Особенности",10131):";".join(features[:10]),
      oz("Материал крестовины",10035):cross if cross.casefold() not in ("нет","n") else "",oz("Материал корпуса",6656):frame,oz("Материал наполнителя",6643):fill_oz,oz("ТН ВЭД коды ЕАЭС",22232):cfg["tnved"],
      oz("Вид выпуска товара",22270):"Фабричное производство",oz("Количество в комплекте, шт.",10119):"1",oz("Количество заводских упаковок",11650):"1",oz("Планирую доставлять товар в нескольких упаковках",22073):"false",
      oz("Страна-изготовитель",4389):cfg["country"],oz("Стиль дизайна",9546):"Современный",oz("Аннотация",4191):desc,oz("Вес товара, г",4383):netg,oz("Вес с упаковкой, г",4497):"31000"}
    if article=="AF-31662421": vals[oz("Фиксация спинки",11260)]="В нескольких положениях"
    if article=="AF-31662372":
        vals[oz("Фиксация спинки",11260)]="В рабочем положении"
        vals[oz("Контроль — Макс. нагрузка",0)]="НЕ ЗАПОЛНЯТЬ: источники расходятся 80/90/150 кг"
    return {k:v for k,v in vals.items() if s(v)!=""}

report={"status":"ВЫПОЛНЯЕТСЯ","marketplace_writes":False,"items":{}}
wa_names={}
for article,cfg in TARGETS.items():
    p=source[article]; st=p["_price_stock"]; purchase=Decimal(str(st["purchase"])); sale=purchase*Decimal("1.23"); old=purchase*Decimal("1.65")
    info=wa.call("shop.product.getInfo",params={"id":cfg["wa_pid"]}); wa_names[article]=s(info.get("name"))
    skus=SYNC.listify(wa.call("shop.product.skus.getList",params={"product_id":cfg["wa_pid"]}),("skus","items")); exact=[x for x in skus if s(x.get("sku"))==article]
    if len(exact)!=1: raise RuntimeError(f"Webasyst {article}: SKU matches {len(exact)}")
    wa.call("shop.product.update",http_method="POST",params={"id":cfg["wa_pid"]},data={"yml_id":cfg["yml"]})
    stock_id=next(iter((exact[0].get("stock") or {"66":0}).keys()),"66")
    wa.call("shop.product.skus.update",http_method="POST",params={"id":s(exact[0].get("id"))},data={"purchase_price":ms(purchase),"price":ms(sale),"compare_price":ms(old),"stock":{stock_id:str(int(st["total"]))}})
    check=wa.call("shop.product.getInfo",params={"id":cfg["wa_pid"]})
    if s(check.get("yml_id"))!=cfg["yml"]: raise RuntimeError(f"Webasyst {article}: yml_id readback failed")
    report["items"][article]={"webasyst":{"product_id":cfg["wa_pid"],"yml_id":cfg["yml"],"sale":ms(sale),"old":ms(old),"purchase":ms(purchase),"url":s(check.get("frontend_url") or check.get("url"))}}

for article,cfg in TARGETS.items():
    p=source[article]; item=item_from_source(article,wa_names[article]); st=p["_price_stock"]; hits=exact_kit(article)
    if len(hits)>1: raise RuntimeError(f"KIT {article}: duplicate exact SKUs {len(hits)}")
    if not hits:
        if article!="AF-31662372": raise RuntimeError(f"KIT {article}: unexpectedly missing")
        vid=SYNC.kit_create(kit,article,item,cfg["category_path"],msk_id,spb_id,cats,chars)
    else: vid=s(hits[0].get("id"))
    full=kit.request("GET",f"/v1/variants/{vid}"); pid=s(full.get("product_id"))
    if not pid: raise RuntimeError(f"KIT {article}: no product_id")
    src_imgs=list(dict.fromkeys([s(x) for x in (p.get("images") or []) if s(x)]))
    media=[m for m in (full.get("media") or []) if isinstance(m,dict) and s(m.get("type")).upper()=="IMAGE"]
    if len(media)!=len(src_imgs):
        newmedia=[]; errs=[]
        for u in src_imgs:
            try:
                up=kit.upload_image_url(u); fid=s(up.get("id"))
                if not fid: raise RuntimeError("no file id")
                newmedia.append({"type":"IMAGE","display_sequence":len(newmedia),"image_id":fid})
            except Exception as e: errs.append({"url":u,"error":str(e)[:500]})
        if len(newmedia)!=len(src_imgs): raise RuntimeError(f"KIT {article}: incomplete images {len(newmedia)}/{len(src_imgs)} {errs}")
        kit.patch_variant(vid,{"media":newmedia}); full=kit.request("GET",f"/v1/variants/{vid}")
    media=[m for m in (full.get("media") or []) if isinstance(m,dict) and s(m.get("type")).upper()=="IMAGE"]
    if len(media)!=len(src_imgs): raise RuntimeError(f"KIT {article}: image readback {len(media)} != {len(src_imgs)}")
    purchase=Decimal(str(st["purchase"])); old=ceilrub(purchase*Decimal("1.65")); sale=ceilrub(purchase*Decimal("1.26"))
    kit.request("POST","/v1/variants/prices/bulk_update",body={"items":[{"variant_id":vid,"price":str(old),"manual_discount_price":str(sale)}]})
    kit.request("POST","/v1/variants/stocks/bulk_update",body={"items":[{"variant_id":vid,"warehouse_id":msk_id,"quantity":int(st["msk"])},{"variant_id":vid,"warehouse_id":spb_id,"quantity":int(st["msk"])}]})
    fm=feature_map(p); pack=fval(fm,"Размер упаковки Д/Ш/В"); nums=[float(x.replace(",",".")) for x in re.split(r"[/xх*]",pack) if s(x)] if pack else []
    if len(nums)!=3: raise RuntimeError(f"{article}: invalid package dimensions {pack!r}")
    box={"length":math.ceil(nums[0]),"width":math.ceil(nums[1]),"height":math.ceil(nums[2]),"weight":31000,"display_sequence":1}
    kit.patch_variant(vid,{"cargo_boxes":[box]})
    full=kit.request("GET",f"/v1/variants/{vid}"); merged=list(full.get("characteristics") or [])
    for title,value in ozon_values(article,p).items(): merged=put_char(merged,title,value)
    kit.patch_variant(vid,{"characteristics":merged})
    full=kit.request("GET",f"/v1/variants/{vid}"); urls=public_urls(vid)
    if len(urls)!=len(src_imgs): raise RuntimeError(f"{article}: KIT public URLs {len(urls)} != source images {len(src_imgs)}")
    SYNC.wa_replace_summary_with_kit_images(wa,cfg["wa_pid"],urls)
    wi=wa.call("shop.product.getInfo",params={"id":cfg["wa_pid"]}); actual_summary=s(wi.get("summary"))
    if s(wi.get("yml_id"))!=cfg["yml"]: raise RuntimeError(f"{article}: yml lost after summary update")
    if sum(1 for u in urls if u in actual_summary)!=len(urls): raise RuntimeError(f"{article}: Webasyst summary KIT URL verification failed")
    prod=kit.request("GET",f"/v1/products/{pid}")
    report["items"][article]["kit"]={"variant_id":vid,"product_id":pid,"kit_id":full.get("kit_id"),"brand":full.get("brand"),"price":str((full.get("pricing") or {}).get("price") or ""),"sale":str((full.get("pricing") or {}).get("manual_discount_price") or ""),"stocks":full.get("stocks"),"cargo_boxes":full.get("cargo_boxes"),"media":full.get("media"),"public_urls":urls,"characteristic_count":len(full.get("characteristics") or []),"category_ids":prod.get("category_ids") or []}
    report["items"][article]["webasyst"]["summary"]=actual_summary
    report["items"][article]["source"]={"images":src_imgs,"msk":st["msk"],"spb":st["spb"],"total":st["total"],"purchase":st["purchase"],"rrp":st["rrp"],"package":pack}

creds=Credentials.from_service_account_info(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"])
gc=gspread.authorize(creds); ws=gc.open_by_key(SID).worksheet(SHEET); vals=ws.get_all_values(); headers=vals[0]; ix={h:i for i,h in enumerate(headers)}
required=["Артикул","YML ID","KIT Цена до скидки","KIT Цена со скидкой","Цена Webasyst","Старая цена Webasyst","Закупочная цена Webasyst","Остаток","Статус источника","Остаток МСК Norden","Остаток СПБ Norden","Остаток всего","KIT product_id","KIT variant_id","KIT ID","KIT media JSON","KIT primary image_id","KIT public media URLs JSON","Тип Webasyst","Остаток Webasyst","Изображения Webasyst links JSON","Статус Webasyst","Фото","Webasyst product_id","Webasyst sku_id","Краткое описание Webasyst","Ozon category_id","Ozon type_id","Ozon категория","Категория Ozon статус","Проверено — загрузить в Ozon и Яндекс.Маркет"]
missing=[h for h in required if h not in ix]
if missing: raise RuntimeError("Sheet missing columns: "+", ".join(missing))
rows_by_article={}
for rn,row in enumerate(vals[1:],2):
    art=s(row[ix["Артикул"]] if ix["Артикул"]<len(row) else "")
    if art in TARGETS: rows_by_article[art]=rn
if set(rows_by_article)!=set(TARGETS): raise RuntimeError(f"Sheet target rows mismatch {rows_by_article}")
updates=[]
def addu(rn,col,val): updates.append({"range":gspread.utils.rowcol_to_a1(rn,ix[col]+1),"values":[[val]]})
for article,cfg in TARGETS.items():
    rn=rows_by_article[article]; x=report["items"][article]; k=x["kit"]; src=x["source"]; w=x["webasyst"]; media=k["media"] or []; primary=s(media[0].get("image_id")) if media else ""
    addu(rn,"YML ID",cfg["yml"]); addu(rn,"KIT Цена до скидки",ceilrub(Decimal(str(src["purchase"]))*Decimal("1.65"))); addu(rn,"KIT Цена со скидкой",ceilrub(Decimal(str(src["purchase"]))*Decimal("1.26")))
    addu(rn,"Цена Webasyst",w["sale"]); addu(rn,"Старая цена Webasyst",w["old"]); addu(rn,"Закупочная цена Webasyst",w["purchase"]); addu(rn,"Остаток",src["total"]); addu(rn,"Статус источника","ЗАГРУЖЕН В WEBASYST И KIT")
    addu(rn,"Остаток МСК Norden",src["msk"]); addu(rn,"Остаток СПБ Norden",src["spb"]); addu(rn,"Остаток всего",src["total"]); addu(rn,"KIT product_id",k["product_id"]); addu(rn,"KIT variant_id",k["variant_id"]); addu(rn,"KIT ID",k["kit_id"])
    addu(rn,"KIT media JSON",jsons(media)); addu(rn,"KIT primary image_id",primary); addu(rn,"KIT public media URLs JSON",jsons(k["public_urls"])); addu(rn,"Тип Webasyst","NORDEN-100"); addu(rn,"Остаток Webasyst",src["total"]); addu(rn,"Изображения Webasyst links JSON",jsons(k["public_urls"])); addu(rn,"Статус Webasyst","СОЗДАН")
    addu(rn,"Фото",jsons(src["images"])); addu(rn,"Webasyst product_id",cfg["wa_pid"])
    skus=SYNC.listify(wa.call("shop.product.skus.getList",params={"product_id":cfg["wa_pid"]}),("skus","items")); ex=[z for z in skus if s(z.get("sku"))==article]; addu(rn,"Webasyst sku_id",s(ex[0].get("id")) if len(ex)==1 else "")
    addu(rn,"Краткое описание Webasyst",w["summary"]); addu(rn,"Ozon category_id",str(OZON_DC)); addu(rn,"Ozon type_id",str(OZON_TYPE)); addu(rn,"Ozon категория","Офисное кресло"); addu(rn,"Категория Ozon статус","ПОДГОТОВЛЕНО В KIT — НЕ ВЫГРУЖЕНО"); addu(rn,"Проверено — загрузить в Ozon и Яндекс.Маркет",False)
for i in range(0,len(updates),100): ws.batch_update(updates[i:i+100],value_input_option="RAW")
vals2=ws.get_all_values()
for article,rn in rows_by_article.items():
    row=vals2[rn-1]; report["items"][article]["sheet_readback"]={"article":row[ix["Артикул"]],"yml":row[ix["YML ID"]],"kit_id":row[ix["KIT ID"]],"market_approval":row[ix["Проверено — загрузить в Ozon и Яндекс.Маркет"]],"ozon_type_id":row[ix["Ozon type_id"]],"ozon_status":row[ix["Категория Ozon статус"]],"status":row[ix["Статус источника"]]}
for article,cfg in TARGETS.items():
    x=report["items"][article]
    if x["sheet_readback"]["market_approval"].upper() not in ("FALSE",""): raise RuntimeError(f"{article}: marketplace approval unexpectedly enabled")
    if s(x["webasyst"]["yml_id"])!=cfg["yml"]: raise RuntimeError(f"{article}: yml report mismatch")
    if not x["kit"].get("kit_id"): raise RuntimeError(f"{article}: KIT ID missing")
report["status"]="УСПЕШНО"; OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"status":report["status"],"marketplace_writes":False,"items":{k:{"kit_id":v["kit"]["kit_id"],"media":len(v["kit"]["media"] or []),"cargo":v["kit"]["cargo_boxes"],"characteristics":v["kit"]["characteristic_count"],"yml":v["webasyst"]["yml_id"],"market_approval":v["sheet_readback"]["market_approval"]} for k,v in report["items"].items()}},ensure_ascii=False,indent=2))
