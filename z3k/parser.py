#!/usr/bin/env python3
"""Z3K.ru -> Webasyst YML Pro. Entire HostCMS sitemap, stable item IDs.
Read only. One native item/color/finish = one independent YML offer.
"""
from __future__ import annotations
import concurrent.futures as futures
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit, urlunsplit, urljoin
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

BASE="https://z3k.ru"
SITEMAP=BASE+"/sitemap.xml"
OUTPUT=Path(os.getenv("OUTPUT","catalog.yml"))
WORKERS=max(1,min(24,int(os.getenv("WORKERS","14"))))
TIMEOUT=int(os.getenv("REQUEST_TIMEOUT","35"))
MIN_OFFERS=int(os.getenv("MIN_OFFERS","1000"))
_LOCAL=threading.local()
CENSOR=re.compile(
    r"(?i)(?:https?://|www\.|[\w.+%-]+@[\w.-]+\.[a-z]{2,}|"
    r"(?:\+7|8\s*\(?800\)?|8\s*\(?495\)?|8\s*\(?812\)?)\s*[\d()\s-]{8,}|"
    r"\b(?:строймашсервис|z3k\.ru|наш(?:ей|ем|его|и)?\s+(?:магазин[ае]?|компани[яию]|сайт[еа]?|"
    r"специалист[ыа]?|менеджер[ыа]?|склад[ае]?)|позвоните|звоните|"
    r"свяжитесь|напишите|консультаци[яию]|оформите заказ|закажите|заказать|"
    r"скидк[аиеу]|акци[яию]|доставк[аиеу]|самовывоз|способ[ыа] оплаты|"
    r"кредит|рассрочк[аиеу]|выгодн[а-я]+\s+цен[а-я]+|"
    r"телефон|адрес|контакты|свяжитесь с нами|"
    r"заполните форму|мы предлагаем|бесплатн[а-я]+\s+доставк[а-я]+)\b)"
)
IMAGE_EXT=re.compile(r"\.(?:jpe?g|png|webp|gif)$",re.I)

def compact(raw):
    return re.sub(r"\s+"," ",str(raw or "").replace("\xa0"," ")).strip()

def sanitize(raw):
    text=str(raw or "")
    parts=re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z])|\n+",text)
    kept=[]
    for fragment in parts:
        value=compact(fragment)
        if not value or CENSOR.search(value):
            continue
        if value not in kept:kept.append(value)
    return " ".join(kept)[:12000]

def sess():
    if not hasattr(_LOCAL,"session"):
        s=requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0 (compatible; Megapolis-YML-Pro/1.0)",
                          "Accept-Language":"ru-RU,ru;q=0.9","Accept-Encoding":"gzip, deflate"})
        _LOCAL.session=s
    return _LOCAL.session

def fetch(url):
    if urlsplit(url).hostname not in ("z3k.ru","www.z3k.ru"):
        raise RuntimeError("Unapproved supplier hostname "+url)
    last=None
    for attempt in range(3):
        try:
            r=sess().get(url,timeout=TIMEOUT)
            if r.status_code in (404,410):
                return None
            r.raise_for_status()
            if len(r.content)<(100 if url.endswith(".xml") else 3000):
                raise RuntimeError("Suspiciously short response")
            r.encoding="utf-8"
            return r.text
        except Exception as exc:
            last=exc
            if attempt<2:
                time.sleep((attempt+1)*1.5)
    raise RuntimeError("Supplier fetch failed: "+url+" "+str(last))

def normalize(url):
    parsed=urlsplit(urljoin(BASE,url))
    if parsed.hostname not in ("z3k.ru","www.z3k.ru"):
        return None
    if not parsed.path.startswith("/shop/") or not parsed.path.endswith("/"):
        return None
    # Strip page tracking, fragments, and language/query duplication.
    return BASE+parsed.path

def listing_urls():
    xml=fetch(SITEMAP)
    if not xml:
        raise RuntimeError("No sitemap")
    root=ET.fromstring(xml)
    products=[]
    for child in root.iter():
        if child.tag.endswith("loc") and child.text:
            url=normalize(child.text)
            if url and url not in products:
                products.append(url)
    if len(products)<2000:
        raise RuntimeError("Sitemap unexpectedly lost its product catalogue: "+str(len(products)))
    print(f"Z3K_DISCOVERY shop_links={len(products)}",flush=True)
    return products

def image_url(raw):
    if not raw:return None
    p=urlsplit(urljoin(BASE,raw))
    if p.hostname not in ("z3k.ru","www.z3k.ru"):
        return None
    if not p.path.startswith("/upload/") or not IMAGE_EXT.search(p.path):
        return None
    return urlunsplit(("https","z3k.ru",p.path,"",""))

def images(soup):
    result=[]
    for a in soup.select(".thumbnails .main-image a[href], #additional-images a[href], .thumbnails [data-zoom-image]"):
        for candidate in (a.get("href"),a.get("data-zoom-image")):
            pic=image_url(candidate)
            if pic and pic not in result:result.append(pic)
    return result[:35]

def parse_price(soup):
    div=soup.select_one(".item-price .diller-price-right")
    if not div:return 0.
    text=compact(div.get_text(" ",strip=True))
    m=re.search(r"\d[\d\s\xa0]*(?:[,.]\d{1,2})?",text)
    if not m: return 0.
    return float(m.group().replace(" ","").replace("\xa0","").replace(",","."))

def parse_properties(soup):
    params={}
    for node in soup.select(".shop_item_properties .shop_property, #attributes .shop_property"):
        span=node.find("span")
        if not span:continue
        value=compact(span.get_text(" ",strip=True)).lstrip(":").strip()
        raw=compact(node.get_text(" ",strip=True))
        key=compact(raw[:raw.rfind(value)]).rstrip(":").strip() if value in raw else ""
        if key and value and len(key)<150 and len(value)<600 and not CENSOR.search(key+" "+value):
            params[key]=value
    return params

def parse_product(html,url):
    soup=BeautifulSoup(html,"html.parser")
    main=soup.select_one('button[data-item-id][id="cart"]')
    if not main:
        # A sitemap entry can be a nested category page, not a product.
        return None
    native_id=compact(main.get("data-item-id"))
    if not native_id.isdigit():
        raise RuntimeError("No HostCMS product ID "+url)
    head=soup.select_one(".shop-item-details h2, .shop_item h2, .item-title h2")
    if head is None:
        # Typical Z3K product title is the last large H2 ahead of the item price.
        region=soup.select_one(".shop_item_properties")
        all_h2=soup.select("h2")
        head=next((h for h in reversed(all_h2) if region is None or h.sourceline is None or h.sourceline<region.sourceline),None)
    name=compact(head.get_text(" ",strip=True)) if head else ""
    if not name or len(name)>350:
        raise RuntimeError("Missing item title for ID "+native_id)
    price=parse_price(soup)
    if price<0:
        raise RuntimeError("Negative price "+native_id)
    params=parse_properties(soup)
    sku=params.pop("Артикул","") or ("Z3K-"+native_id)
    vendor=params.pop("Производитель","")
    # Manufacturer is a product fact, but never export supplier shop identity.
    if vendor and not CENSOR.search(vendor):
        params["Бренд"]=vendor
    desc_node=soup.select_one(".shop_item_properties .item-description")
    description=sanitize(desc_node.get_text("\n",strip=True)) if desc_node else ""
    available=bool(soup.select_one(".nalichiei, .nalichie, .in-stock, .is-available"))
    if soup.select_one(".whaiting, .waiting, .out-of-stock, .no-stock"):
        available=False
    if not price:available=False
    photos=images(soup)
    if not photos:
        raise RuntimeError("Product "+native_id+" has no photos")
    # The sitemap is hierarchical; last parent section identifies the category.
    category=urlsplit(url).path.rstrip("/").split("/")[-2].replace("-"," ").capitalize()
    return {
        "id":"z3k-"+native_id,"name":name,"price":price,"available":available,
        "sku":sku,"url":url,"category":category,
        "photos":photos,"params":params,"description":description
    }

def generate(products):
    root=ET.Element("yml_catalog",{"date":datetime.now().strftime("%Y-%m-%d %H:%M")})
    shop=ET.SubElement(root,"shop")
    ET.SubElement(shop,"name").text="Мегаполис - инструменты и оборудование"
    ET.SubElement(shop,"company").text="ООО Мегаполис"
    ET.SubElement(shop,"url").text="https://profikompany.ru/"
    currencies=ET.SubElement(shop,"currencies")
    ET.SubElement(currencies,"currency",{"id":"RUR","rate":"1"})
    cats=ET.SubElement(shop,"categories")
    categories=sorted({p["category"] for p in products})
    ids={c:str(int(hashlib.sha1(c.encode()).hexdigest()[:9],16)) for c in categories}
    for category in categories:
        ET.SubElement(cats,"category",{"id":ids[category]}).text=category
    offers=ET.SubElement(shop,"offers")
    for p in sorted(products,key=lambda v:v["id"]):
        offer=ET.SubElement(offers,"offer",{"id":p["id"],"available":"true" if p["available"] else "false"})
        for tag,value in (("url",p["url"]),("price","%g" % p["price"]),
                          ("currencyId","RUR"),("categoryId",ids[p["category"]])):
            ET.SubElement(offer,tag).text=value
        for photo in p["photos"]:
            ET.SubElement(offer,"picture").text=photo
        ET.SubElement(offer,"name").text=p["name"]
        ET.SubElement(offer,"vendorCode").text=p["sku"]
        if p["description"]:
            ET.SubElement(offer,"description").text=p["description"]
        for name,value in p["params"].items():
            if not CENSOR.search(name+" "+value):
                ET.SubElement(offer,"param",{"name":name}).text=value
        if not p["price"]:
            ET.SubElement(offer,"param",{"name":"Цена по запросу"}).text="Да"
    ET.indent(root,space="  ")
    return ET.tostring(root,encoding="utf-8",xml_declaration=True)

def verify(data):
    root=ET.fromstring(data)
    offers=root.findall("./shop/offers/offer")
    ids=[o.get("id") for o in offers]
    if len(offers)<MIN_OFFERS or len(set(ids))!=len(ids):
        raise RuntimeError("Missing products or duplicated HostCMS IDs")
    for o in offers:
        if not o.findtext("name") or not o.findtext("vendorCode") or not o.findall("picture"):
            raise RuntimeError("Incomplete product "+str(o.get("id")))
        if CENSOR.search(o.findtext("description") or ""):
            raise RuntimeError("Supplier advertisement leaked")
    if OUTPUT.exists():
        old=ET.parse(OUTPUT).getroot().findall("./shop/offers/offer")
        prior={o.get("id") for o in old}
        missing=prior-set(ids)
        if prior and (len(ids)<len(prior)*.88 or len(missing)>max(20,len(prior)*.15)):
            raise RuntimeError("Old catalogue would lose "+str(len(missing))+" items")
    return len(offers),sum(len(o.findall("picture")) for o in offers)

def main():
    urls=listing_urls()
    errors=[]
    missing=[]
    products={}
    with futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        jobs={pool.submit(fetch,url):url for url in urls}
        for n,future in enumerate(futures.as_completed(jobs),1):
            url=jobs[future]
            try:
                html=future.result()
                if html is None:
                    missing.append(url)
                    continue
                result=parse_product(html,url)
                if result is None:continue
                if result["id"] in products:
                    # The sitemap may repeat a product in multiple subcategories.
                    current=products[result["id"]]
                    if current["sku"]!=result["sku"]:
                        raise RuntimeError("ID collision with conflicting SKU "+result["id"])
                else:products[result["id"]]=result
            except Exception as exc:
                errors.append(url+" "+str(exc))
            if n%100==0 or n==len(urls):
                print("Z3K_PROGRESS %d/%d offers=%d missing=%d errors=%d" % (n,len(urls),len(products),len(missing),len(errors)),flush=True)
    if len(missing)>max(15,len(urls)*.04):
        raise RuntimeError("Too many deleted pages: "+str(len(missing)))
    if errors:
        for err in errors[:25]:print("Z3K_FAIL "+err,flush=True)
        raise RuntimeError("%d supplier pages failed; leave previous YML untouched" % len(errors))
    payload=generate(list(products.values()))
    count,photo_count=verify(payload)
    tmp=OUTPUT.with_suffix(".yml.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp,OUTPUT)
    print("Z3K_READY offers=%d pictures=%d skipped_missing=%d bytes=%d" % (count,photo_count,len(missing),len(payload)),flush=True)

if __name__=="__main__":
    main()
