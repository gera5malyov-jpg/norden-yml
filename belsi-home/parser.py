#!/usr/bin/env python3
"""BELSI HOME -> safe, weekly YML Pro. Each true Bitrix offer (color/other options)
is a distinct offer, with its own pictures, SKU, price and stable ID.
"""
import concurrent.futures as cf
import hashlib
import os
from pathlib import Path
import re
import threading
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse, parse_qs
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
import chompjs

BASE="https://belsi-home.ru"
SITEMAP=BASE+"/sitemap.xml"
OUTPUT=Path(os.getenv("OUTPUT","catalog.yml"))
WORKERS=max(1,min(7,int(os.getenv("WORKERS","4"))))
MIN_OFFERS=int(os.getenv("MIN_OFFERS","100"))
TIMEOUT=35
_LOCAL=threading.local()
CONTACTS=re.compile(
    r"(?i)(?:https?://|www\.|[\w.+-]+@[\w.-]+\.[a-z]{2,}|"
    r"(?:\+7|8\s*\(?800\)?|8\s*\(?495\)?|8\s*\(?812\)?)\s*[\d\s()-]{8,}|"
    r"\b(?:контакт(?:ы|ная)?|телефон(?:ы)?|адрес(?:а)?|почта|"
    r"доставк[аиуе]|оплат[аиу]|самовывоз|менеджер(?:ы|а|у|ом)?|"
    r"заказ(?:ать|ывайте|ывать)|наш(?:ем|ей|а|и|е)?\s+(?:сайте|магазине|"
    r"компани[юией]|офисе|специалист(?:ы|ам|ов))|"
    r"позвоните|звоните|напишите|свяжитесь|скидк[ауие]|акци[яию]|"
    r"покупа[йте]|закажите|мы предлагаем|мы рады|"
    r"бесплатная консультация|консультаци[яию]|"
    r"продаж[аиу]|товары нашего магазина)\b)"
)
ORIGINAL_RE=re.compile(
    r"^/upload/resize_cache/(iblock|uf)/(.+?)/([^/]+\.(?:jpe?g|png|webp))$",re.I)
SIZE_RE=re.compile(r"^\d+_\d+_[0-9a-z]+$",re.I)
IMAGE_RE=re.compile(r"^/upload/(?:iblock|uf)/.+\.(?:jpe?g|png|webp)$",re.I)

def clean(x):
    return re.sub(r"\s+"," ",str(x or "").replace("\xa0"," ")).strip()

def sanitize(x):
    paragraphs=re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z])|\n+|[•●]+",str(x or ""))
    kept=[]
    for sentence in paragraphs:
        s=clean(sentence)
        if not s or CONTACTS.search(s) or s.lower().startswith(("подробнее", "оформить","позвон")):
            continue
        if s not in kept:
            kept.append(s)
    return clean(" ".join(kept))[:14000]

def session():
    if not hasattr(_LOCAL,"session"):
        s=requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0 (compatible; MegapolisSupplierFeed/1.0)",
                          "Accept-Language":"ru-RU,ru;q=0.9"})
        _LOCAL.session=s
    return _LOCAL.session

def fetch(url):
    if urlparse(url).hostname not in ("belsi-home.ru","www.belsi-home.ru"):
        raise RuntimeError("External fetch blocked: "+url)
    last=None
    for n in range(3):
        try:
            response=session().get(url,timeout=TIMEOUT)
            if response.status_code==404:
                return None
            response.raise_for_status()
            if len(response.content)<(100 if urlparse(url).path.endswith(".xml") else 1200):
                raise RuntimeError("Truncated page")
            response.encoding=response.apparent_encoding or "utf-8"
            return response.text
        except Exception as err:
            last=err
            time.sleep(n+1)
    raise RuntimeError("Cannot read "+url+": "+str(last))

def canonical(url):
    p=urlparse(urljoin(BASE,url))
    if p.hostname not in ("belsi-home.ru","www.belsi-home.ru"):
        return None
    if not p.path.startswith("/catalog/") or not p.path.endswith("/"):
        return None
    return BASE+p.path

def sitemap_urls():
    root=ET.fromstring(fetch(SITEMAP))
    index=[x.text.strip() for x in root.iter() if x.tag.endswith("loc") and x.text]
    result=[]
    for sitemap in index:
        if not sitemap.startswith(BASE+"/sitemap-iblock-"):
            continue
        page=fetch(sitemap)
        if page is None:
            raise RuntimeError("Missing sitemap "+sitemap)
        xml=ET.fromstring(page)
        for x in xml.iter():
            if x.tag.endswith("loc") and x.text:
                v=canonical(x.text.strip())
                if v and v not in result:
                    result.append(v)
    if len(result)<180:
        raise RuntimeError("Sitemap unexpectedly short: %d links" % len(result))
    print("BELSI_DISCOVER catalog_links=%d" % len(result),flush=True)
    return result

def full_image(src):
    if not src:
        return None
    p=urlparse(urljoin(BASE,src))
    if p.hostname not in ("belsi-home.ru","www.belsi-home.ru"):
        return None
    path=p.path
    m=ORIGINAL_RE.fullmatch(path)
    if m:
        sections=m[2].split("/")
        # Bitrix resizes may have nested hashed media directories; strip
        # only the size transformation segment, retaining every real directory.
        sections=[s for s in sections if not SIZE_RE.fullmatch(s)]
        path="/upload/"+m[1]+"/"+"/".join(sections)+"/"+m[3]
    if not IMAGE_RE.fullmatch(path):
        return None
    return BASE+path

def pictures_from_offer(offer):
    gallery=offer.get("SLIDER") or []
    candidates=[x.get("SRC") for x in gallery if isinstance(x,dict)]
    for field in ("DETAIL_PICTURE","PREVIEW_PICTURE"):
        pic=offer.get(field) or {}
        if isinstance(pic,dict):
            candidates.append(pic.get("SRC"))
    result=[]
    for item in candidates:
        v=full_image(item)
        if v and v not in result:
            result.append(v)
    return result[:35]

def option_map(soup):
    mapping={}
    for el in soup.select("[data-onevalue][data-color]"):
        mapping[("PROP_31",str(el.get("data-onevalue")))]=clean(el.get("data-color"))
    for container in soup.select("[data-propname]"):
        key=container.get("data-propname") or ""
        match=re.search(r"prop_(\d+)_cont",container.get("id") or "")
        if not match:
            continue
        prop="PROP_"+match.group(1)
        for item in container.select("[data-onevalue]"):
            value=clean(item.get("title") or item.get("data-color") or item.get_text(" ",strip=True))
            if value:
                mapping[(prop,str(item.get("data-onevalue")))]=value
    return mapping

def js_block(raw, marker, opener="{", closer="}"):
    """Extract one balanced JS object/array, respecting quotes, escaped quotes,
    inline HTML and literal newlines in Bitrix's not-quite-valid JS strings."""
    start=raw.find(marker)
    if start<0:
        return None
    start=raw.find(opener,start+len(marker))
    if start<0:
        return None
    depth=0
    quoted=None
    escaped=False
    for i in range(start,len(raw)):
        c=raw[i]
        if quoted:
            if escaped:
                escaped=False
            elif c=="\\":
                escaped=True
            elif c==quoted:
                quoted=None
            continue
        if c in ("'",'"'):
            quoted=c
        elif c==opener:
            depth+=1
        elif c==closer:
            depth-=1
            if depth==0:
                return raw[start:i+1]
    raise RuntimeError("Unbalanced website script around "+marker)

def js_string(raw,key,default=""):
    pattern=r"'"+re.escape(key)+r"':'((?:\\.|[^'\\])*)'"
    m=re.search(pattern,raw,re.S)
    if not m:
        return default
    return m.group(1).replace("\\/","/").replace("\\'","'").replace("\\\\","\\")

def partial_offer(raw):
    """Parse only fields needed by YML without JSON-decoding embedded HTML."""
    obj={}
    obj["ID"]=js_string(raw,"ID")
    obj["NAME"]=js_string(raw,"NAME")
    obj["CAN_BUY"]=bool(re.search(r"'CAN_BUY':true",raw))
    tree=js_block(raw,"'TREE':")
    obj["TREE"]={("PROP_"+x):y for x,y in re.findall(r"'PROP_(\d+)':'([^']+)'",tree or "")}
    value=js_block(raw,"'PRICE':")
    price = (js_string(value or "","DISCOUNT_VALUE") or js_string(value or "","VALUE"))
    obj["PRICE"]={"DISCOUNT_VALUE":price} if price else {}
    props=re.search(r"'DISPLAY_PROPERTIES':'((?:\\.|[^'\\])*)'",raw,re.S)
    obj["DISPLAY_PROPERTIES"]=props.group(1) if props else ""
    slider=js_block(raw,"'SLIDER':","[","]")
    obj["SLIDER"]=[{"SRC":m} for m in re.findall(r"'SRC':'((?:\\.|[^'\\])*)'",slider or "",re.S)]
    # For standalone products, PICT contains primary image, but not other images.
    pict=js_block(raw,"'PICT':")
    obj["PICT"]={"SRC":js_string(pict or "","SRC")}
    return obj

def extract_offer_script(html):
    key="new JCCatalogElement("
    start=html.find(key)
    if start<0:
        return None
    raw=html[start+len(key):]
    product_type=js_string(raw[:600],"PRODUCT_TYPE")
    block=js_block(raw,"'PRODUCT':")
    if not block:
        raise RuntimeError("Missing PRODUCT in Bitrix script")
    product=partial_offer(block)
    # Simpler standalone products use PRODUCT_TYPE=1 and no OFFERS.
    # Multicolor products use product_type=3 with all actual variant SKUs.
    offers_block=js_block(raw,"'OFFERS':","[","]")
    offers=[]
    if offers_block:
        positions=[x.start() for x in re.finditer(r"\{'ID':'\d+','NAME':",offers_block)]
        for idx,pos in enumerate(positions):
            piece=offers_block[pos:(positions[idx+1] if idx+1<len(positions) else len(offers_block))]
            offers.append(partial_offer(piece))
    return {"PRODUCT":product,"PRODUCT_TYPE":product_type,"OFFERS":offers}

def category_from_soup(soup,url):
    breadcrumbs=[clean(x.get_text(" ",strip=True)) for x in soup.select(".breadcrumb__title")]
    breadcrumbs=[x for x in breadcrumbs if x and x.lower() not in ("каталог","главная","назад")]
    if breadcrumbs:
        return breadcrumbs[-1][:150]
    seg=urlparse(url).path.split("/")
    return (seg[-3] if len(seg)>3 else "Товары")[:150]

def parsed_params(soup):
    params={}
    for group in soup.select(".detail__properties, .detail__characteristics, .detail__props"):
        for row in group.select("tr,li,dt"):
            raw=clean(row.get_text(" ",strip=True))
            if ":" in raw:
                k,v=raw.split(":",1)
                if clean(k) and clean(v) and not CONTACTS.search(k+v):
                    params[clean(k)]=clean(v)
    return params

def sku_from_offer(offer):
    prop=offer.get("DISPLAY_PROPERTIES") or ""
    text=BeautifulSoup(str(prop).replace("\\/","/"),"html.parser")
    dts=text.find_all("dt")
    for dt in dts:
        if "артикул" in clean(dt.get_text()).lower():
            dd=dt.find_next_sibling("dd")
            if dd:
                return clean(dd.get_text(" ",strip=True))
    return ""

def parse_page(html,url):
    if html is None:
        return [], []
    script=extract_offer_script(html)
    soup=BeautifulSoup(html,"html.parser")
    if script is None:
        # Section pages are not separate products; gather only actual item
        # links from the catalogue grid to find unindexed products.
        links=[]
        for a in soup.select(".catalog__item-link[href],a.catalog__item-link[href],a.catalog-item[href]"):
            v=canonical(a.get("href") or "")
            if v and v!=url and v not in links:
                links.append(v)
        return [],links
    parent=script.get("PRODUCT") or {}
    group=clean(parent.get("ID") or "")
    category=category_from_soup(soup,url)
    desc_tag=soup.select_one(".detail__text-content")
    description=sanitize(desc_tag.get_text("\n",strip=True) if desc_tag else "")
    spec=parsed_params(soup)
    vals=option_map(soup)
    offers=script.get("OFFERS") or []
    if not isinstance(offers,list):
        raise RuntimeError("Unrecognized Bitrix offers list "+url)
    items=[]
    for offer in offers:
        if not isinstance(offer,dict):
            continue
        oid=clean(offer.get("ID"))
        if not oid.isdecimal():
            raise RuntimeError("Missing SKU ID in "+url)
        price_raw=(offer.get("PRICE") or {}).get("DISCOUNT_VALUE")
        if price_raw is None:
            price_raw=(offer.get("PRICE") or {}).get("VALUE")
        price=float(clean(price_raw).replace(" ","").replace(",",".")) if price_raw not in (None,"") else 0.0
        if price<0:
            raise RuntimeError("Negative SKU price "+oid)
        name=clean(offer.get("NAME") or parent.get("NAME"))
        if not name:
            raise RuntimeError("Empty SKU name "+oid)
        photos=pictures_from_offer(offer)
        if not photos:
            raise RuntimeError("Missing SKU photos for "+oid+" "+url)
        opt={}
        for k,v in (offer.get("TREE") or {}).items():
            val=vals.get((k,str(v)))
            if val:
                label="Цвет" if k=="PROP_31" else ("Ориентация" if k=="PROP_56" else "Модификация "+k)
                opt[label]=val
        product_url=url+"?model="+oid
        items.append({
            "id":"belsi-"+oid,"group":"belsi-group-"+(group or oid),
            "url":product_url,"name":name,"sku":sku_from_offer(offer) or "BELSI-"+oid,
            "price":price,"available":bool(offer.get("CAN_BUY",False)) and price>0,
            "pictures":photos,"description":description,
            "params":dict(spec,**opt),"category":category,
        })
    if not items:
        # A Bitrix product without variants must be revisited as an issue;
        # do not silently replace it with unrelated section products.
        raise RuntimeError("Product object has no offers: "+url)
    return items,[]

def create_yml(products):
    root=ET.Element("yml_catalog",{"date":datetime.now().strftime("%Y-%m-%d %H:%M")})
    shop=ET.SubElement(root,"shop")
    for k,v in (("name","Мегаполис - каталог BELSI"),("company","ООО Мегаполис"),
                ("url","https://profikompany.ru/")):
        ET.SubElement(shop,k).text=v
    cur=ET.SubElement(shop,"currencies")
    ET.SubElement(cur,"currency",{"id":"RUR","rate":"1"})
    cats=ET.SubElement(shop,"categories")
    categories=sorted({p["category"] for p in products})
    category_ids={c:str(int(hashlib.sha1(c.encode()).hexdigest()[:9],16)) for c in categories}
    for c in categories:
        ET.SubElement(cats,"category",{"id":category_ids[c]}).text=c
    offers=ET.SubElement(shop,"offers")
    for p in sorted(products,key=lambda x:x["id"]):
        offer=ET.SubElement(offers,"offer",{"id":p["id"],"available":"true" if p["available"] else "false", "group_id":p["group"]})
        for tag,value in (("url",p["url"]),("price","%g" % p["price"]),
                          ("currencyId","RUR"),("categoryId",category_ids[p["category"]])):
            ET.SubElement(offer,tag).text=value
        for src in p["pictures"]:
            ET.SubElement(offer,"picture").text=src
        ET.SubElement(offer,"name").text=p["name"]
        ET.SubElement(offer,"vendorCode").text=p["sku"]
        if p["description"]:
            ET.SubElement(offer,"description").text=p["description"]
        for k,v in p["params"].items():
            if not CONTACTS.search(k+" "+v):
                ET.SubElement(offer,"param",{"name":k}).text=v
    ET.indent(root,space="  ")
    return ET.tostring(root,encoding="utf-8",xml_declaration=True)

def validate(xml):
    root=ET.fromstring(xml)
    offers=root.findall("./shop/offers/offer")
    ids=[o.get("id") for o in offers]
    if len(offers)<MIN_OFFERS or len(set(ids))!=len(ids):
        raise RuntimeError("Too few offers or duplicate variant IDs")
    for o in offers:
        if not (o.findtext("name") and o.findtext("vendorCode") and o.findall("picture") and "?model=" in (o.findtext("url") or "")):
            raise RuntimeError("Incomplete variant "+str(o.get("id")))
        if CONTACTS.search(o.findtext("description") or ""):
            raise RuntimeError("Supplier contact in description")
        if any("/resize_cache/" in (pic.text or "") for pic in o.findall("picture")):
            raise RuntimeError("Resized image in YML")
    if OUTPUT.exists():
        old=ET.parse(OUTPUT).getroot().findall("./shop/offers/offer")
        old_ids={o.get("id") for o in old}
        missing=old_ids-set(ids)
        if old_ids and (len(ids)<.85*len(old_ids) or len(missing)>max(20,len(old_ids)*.15)):
            raise RuntimeError("Unsafe missing old variants: "+str(len(missing)))
    return len(offers),sum(len(o.findall("picture")) for o in offers)

def main():
    pages=sitemap_urls()
    by_id={}
    failures=[]
    missing404=0
    extra=set()
    def worker(url):
        raw=fetch(url)
        if raw is None: return url,None,None
        a,b=parse_page(raw,url)
        return url,a,b
    # Sitemap contains both products and categories. Limit extra discovery
    # to links located within actual category grids, not header/footer menus.
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        fut={pool.submit(worker,url):url for url in pages}
        for num,f in enumerate(cf.as_completed(fut),1):
            url=fut[f]
            try:
                u,items,links=f.result()
                if items is None:
                    missing404+=1
                    continue
                for item in items:
                    old=by_id.get(item["id"])
                    if old is None or len(item["pictures"])>len(old["pictures"]):
                        by_id[item["id"]]=item
                for link in links:
                    if link not in pages:
                        extra.add(link)
            except Exception as err:
                failures.append(url+": "+str(err))
            if num%50==0:
                print("BELSI_PROGRESS pages=%d/%d offers=%d failures=%d" % (num,len(pages),len(by_id),len(failures)),flush=True)
        if extra:
            print("BELSI_EXTRA category_links=%d" % len(extra),flush=True)
            more={pool.submit(worker,u):u for u in sorted(extra)[:800]}
            for f in cf.as_completed(more):
                u=more[f]
                try:
                    _,items,_=f.result()
                    for item in items or []:
                        if item["id"] not in by_id:
                            by_id[item["id"]]=item
                except Exception as e:
                    failures.append(u+": "+str(e))
    if failures:
        for e in failures[:15]: print("BELSI_FAIL "+e,flush=True)
        raise RuntimeError("%d pages failed; keep previous YML" % len(failures))
    data=create_yml(list(by_id.values()))
    offers,imgs=validate(data)
    temp=OUTPUT.with_suffix(".tmp")
    temp.write_bytes(data)
    os.replace(temp,OUTPUT)
    print("BELSI_READY pages=%d missing404=%d offers=%d images=%d bytes=%d" % (len(pages),missing404,offers,imgs,len(data)),flush=True)

if __name__=="__main__":
    main()
