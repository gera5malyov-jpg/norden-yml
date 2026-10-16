#!/usr/bin/env python3
"""METTA: unique color/finish/other offer -> distinct YML Pro product.
Supplier contacts and marketing never flow into product descriptions.
"""
from __future__ import annotations
import concurrent.futures as cf
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qs, urljoin
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup

BASE="https://www.metta.ru"
SITEMAP=BASE+"/sitemap-products.xml"
OUTPUT=Path(os.getenv("OUTPUT","catalog.yml"))
MIN_OFFERS=int(os.getenv("MIN_OFFERS","120"))
WORKERS=max(1,min(12,int(os.getenv("WORKERS","8"))))
TIMEOUT=int(os.getenv("TIMEOUT","40"))
_LOCAL=threading.local()
CONTACT=re.compile(
    r"(?i)(?:https?://|www\.|[\w.+%-]+@[\w.-]+\.[a-z]{2,}|"
    r"(?:\+7|8\s*\(?800\)?|8\s*\(?495\)?|8\s*\(?812\)?)\s*[\d()\s-]{8,}|"
    r"\b(?:позвоните|звоните|напишите|оформите|заказывайте|купите|"
    r"закажите|свяжитесь|менеджер(?:ом|у|а|ы)?|консультация|"
    r"доставк[аиеу]|оплат[аиеу]|самовывоз|магазин(?:ы|е|а)?|"
    r"наш(?:ем|ей|и|е|а)?\s+(?:сайте|магазине|салон[ае]|компани[юией])|"
    r"акци[яию]|скидк[аиеу]|рассрочк[аиеу]|кредит|"
    r"уникальное предложение|успейте|подарок|распродажа|"
    r"официальном сайте|выгодной цене|контакт(?:ы|ный)?|телефон(?:ы)?|"
    r"оставьте заявку)\b)"
)
PHOTO_ALLOWED={"www.metta.ru","metta.ru","3do.metta.ru"}
PHOTO_EXT=re.compile(r"\.(?:jpg|jpeg|webp|png|avif)$",re.I)
RESIZE=re.compile(r"^/upload/resize_cache/(iblock|uf)/(.+)/([^/]+\.(?:jpg|jpeg|png|webp))$",re.I)
SIZE=re.compile(r"^\d+_\d+_[a-z0-9]+$",re.I)
VALID_PRODUCT=re.compile(r"^/catalog/[^/?#]+/[^/?#]+/$")

def clean(s):
    return re.sub(r"\s+"," ",str(s or "").replace("\xa0"," ")).strip()

def scrub(text):
    lines=re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z])|\n+",str(text or ""))
    out=[]
    for line in lines:
        s=clean(line)
        if s and not CONTACT.search(s) and s not in out:
            out.append(s)
    return " ".join(out)[:10000]

def session():
    if not hasattr(_LOCAL,"s"):
        s=requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0 (compatible; Megapolis-YMLPro/1.0)",
                          "Accept-Language":"ru-RU,ru;q=0.9"})
        _LOCAL.s=s
    return _LOCAL.s

def get(url):
    if urlsplit(url).hostname not in {"metta.ru","www.metta.ru"}:
        raise RuntimeError("Unapproved URL "+url)
    last=None
    for trial in range(3):
        try:
            res=session().get(url,timeout=TIMEOUT)
            res.raise_for_status()
            if len(res.content)<(150 if urlsplit(url).path.endswith(".xml") else 4000):
                raise RuntimeError("Truncated supplier response")
            res.encoding=res.apparent_encoding or "utf-8"
            return res.text
        except Exception as exc:
            last=exc
            if trial<2: time.sleep(1.4*(trial+1))
    raise RuntimeError("Supplier request failed: %s - %s" % (url,last))

def product_base(url):
    p=urlsplit(urljoin(BASE,url))
    if p.hostname not in {"metta.ru","www.metta.ru"}:
        return None
    path=p.path
    if path.startswith("/spb/catalog/"):
        path="/catalog/"+path[len("/spb/catalog/"):]
    if VALID_PRODUCT.fullmatch(path):
        return BASE+path
    return None

def offer_url(base,offer_id):
    return base+"?offer="+str(offer_id)

def discover_models():
    tree=ET.fromstring(get(SITEMAP))
    from_sitemap=[]
    for e in tree.iter():
        if e.tag.endswith("loc") and e.text:
            v=product_base(e.text.strip())
            if v and v not in from_sitemap:
                from_sitemap.append(v)
    if len(from_sitemap)<65:
        raise RuntimeError("Unusually small official sitemap: "+str(len(from_sitemap)))
    # Supplement the official 95-model product sitemap with the visible SPB
    # storefront. The sitemap can omit current outlet/catalogue items.
    extras=set()
    for page in [BASE+"/spb/catalogue/",BASE+"/spb/catalog/samurai/",
                 BASE+"/spb/catalog/yogax2/",BASE+"/spb/catalog/move/",
                 BASE+"/spb/catalog/equalizer/"]:
        html=get(page)
        for href in re.findall(r'href=["\']([^"\']+/catalog/[^"\']+)',html):
            v=product_base(href.split("#")[0])
            if v: extras.add(v)
    all_models=sorted(set(from_sitemap)|extras)
    if len(all_models)<len(from_sitemap):
        raise RuntimeError("Discovery inexplicably lost models")
    print("METTA_DISCOVER sitemap=%d extra=%d model_pages=%d" % (len(from_sitemap),len(extras-set(from_sitemap)),len(all_models)),flush=True)
    return all_models

def variant_links(html,base):
    doc=BeautifulSoup(html,"html.parser")
    page=doc.select_one(".ds-catalog-detail-page")
    if page is None:
        raise RuntimeError("No METTA product detail block "+base)
    found={}
    for a in page.select('a[href*="offer="]'):
        href=a.get("href") or ""
        if product_base(href)!=base:
            continue
        values=parse_qs(urlsplit(href).query).get("offer",[])
        if not values or not values[0].isdigit():
            continue
        oid=values[0]
        found[oid]={
            "color":clean(a.get("data-color")),
            "sku":clean(a.get("data-code")),
            "image":a.get("data-left-image") or "",
            "back":a.get("data-right-image") or "",
        }
    element=page.select_one("[data-fl-item-id]")
    if not element:
        raise RuntimeError("No selected SKU on "+base)
    selected=clean(element.get("data-fl-item-id"))
    if not selected.isdigit():
        raise RuntimeError("Invalid selected SKU "+base)
    found.setdefault(selected,{"color":"","sku":"","image":"","back":""})
    if len(found)>120:
        raise RuntimeError("Unexpected number of variants "+base)
    return found

def picture(url):
    if not url: return None
    u=urlsplit(urljoin(BASE,url))
    if u.hostname not in PHOTO_ALLOWED: return None
    path=u.path
    m=RESIZE.fullmatch(path)
    if m:
        dirs=[s for s in m.group(2).split("/") if not SIZE.fullmatch(s)]
        path="/upload/"+m.group(1)+"/"+"/".join(dirs)+"/"+m.group(3)
    if not PHOTO_EXT.search(path): return None
    if u.hostname!="3do.metta.ru" and not path.startswith("/upload/"): return None
    return urlunsplit(("https",u.hostname,path,"",""))

def photos(soup,code):
    selected=soup.select_one(".ds-catalog-detail-page")
    pics=[]
    if not selected: return []
    for img in selected.select(".ds-detail-image-gallery img[data-ds-image], .ds-detail-image-gallery .swiper-slide img"):
        url=picture(img.get("data-src") or img.get("src"))
        if url and url not in pics:
            pics.append(url)
    meta=selected.select_one('meta[itemprop="image"]')
    if meta:
        url=picture(meta.get("content"))
        if url and url not in pics: pics.insert(0,url)
    if not pics and code:
        for fmt in ("x2048","x2048/h270","x2048/back"):
            url=picture("https://3do.metta.ru/render/model/"+fmt+"/"+code+".webp")
            if url and url not in pics:pics.append(url)
    return pics[:25]

def specs(soup):
    data={}
    root=soup.select_one(".ds-catalog-detail-page")
    if root is None: return data
    for table in root.select(".ds-detail-props-table, .ds-tabs-content[data-content='props']"):
        for row in table.select(".py-1x"):
            k=row.select_one(".text-gray-950")
            v=row.select_one(".text-gray-800")
            if not k or not v:continue
            key,val=clean(k.get_text(" ",strip=True)),clean(v.get_text(" ",strip=True))
            if key and val and len(key)<160 and len(val)<280 and not CONTACT.search(key+" "+val):
                data[key]=val
    return data

def describe(name,params):
    # The supplier's long "description" is marketing copy with unverifiable
    # promises. Compose strictly factual text from published specifications.
    facts=[]
    for key,value in params.items():
        if key.lower().startswith("гарантия") or CONTACT.search(key+" "+value):
            continue
        facts.append(key+": "+value)
    return scrub(name+". "+". ".join(facts[:18])+".")[:2500]

def parse_offer(html,base,requested_id,known):
    soup=BeautifulSoup(html,"html.parser")
    main=soup.select_one(".ds-catalog-detail-page")
    if main is None:
        raise RuntimeError("No product content in "+base)
    track=main.select_one("[data-fl-item-id]")
    if track is None:
        raise RuntimeError("Offer tracking data missing")
    actual=clean(track.get("data-fl-item-id"))
    if actual!=requested_id:
        raise RuntimeError("Wrong variant: requested %s got %s" % (requested_id,actual))
    price_text=clean(track.get("data-price"))
    try:price=float(price_text.replace(" ","").replace(",","."))
    except ValueError:raise RuntimeError("Missing price for SKU "+actual)
    if price<0:raise RuntimeError("Negative price SKU "+actual)
    available=clean(track.get("data-fl-item-available")).lower() in ("true","1","yes")
    title=main.select_one("h1")
    name=clean(title.get_text(" ",strip=True) if title else "")
    if not name:
        # Fallback to breadcrumb if an image-styled heading.
        meta=main.select_one('meta[itemprop="name"]')
        name=clean(meta.get("content") if meta else "")
    if not name:
        raise RuntimeError("Product name absent for "+actual)
    p=specs(soup)
    selected=known.get(actual,{})
    color=selected.get("color") or p.get("Цвет") or ""
    if color:
        p["Цвет"]=color
        if color.lower() not in name.lower():
            name=name+" - "+color
    code=selected.get("sku")
    if code: p["Код модели"]=code
    paths=photos(soup,code)
    if not paths and selected.get("image"):
        v=picture(selected["image"])
        if v:paths.append(v)
    if not paths:
        raise RuntimeError("Variant has no photo "+actual)
    group_id="metta-group-"+base.rstrip("/").split("/")[-1]
    category=base.rstrip("/").split("/")[-2].replace("_"," ").capitalize()
    return {"id":"metta-"+actual,"group":group_id,"url":offer_url(base,actual),
            "name":name,"sku":code or "METTA-"+actual,"price":price,
            "available":available and price>0,"category":category,
            "params":p,"photos":paths,"description":describe(name,p)}

def build(products):
    root=ET.Element("yml_catalog",{"date":datetime.now().strftime("%Y-%m-%d %H:%M")})
    shop=ET.SubElement(root,"shop")
    ET.SubElement(shop,"name").text="Мегаполис - каталог METTA"
    ET.SubElement(shop,"company").text="ООО Мегаполис"
    ET.SubElement(shop,"url").text="https://profikompany.ru/"
    cur=ET.SubElement(shop,"currencies")
    ET.SubElement(cur,"currency",{"id":"RUR","rate":"1"})
    cats=ET.SubElement(shop,"categories")
    categories=sorted({x["category"] for x in products})
    mapping={c:str(int(hashlib.sha1(c.encode()).hexdigest()[:9],16)) for c in categories}
    for name in categories:
        ET.SubElement(cats,"category",{"id":mapping[name]}).text=name
    out=ET.SubElement(shop,"offers")
    for x in sorted(products,key=lambda r:r["id"]):
        o=ET.SubElement(out,"offer",{"id":x["id"],"available":"true" if x["available"] else "false","group_id":x["group"]})
        for k,v in (("url",x["url"]),("price","%g" % x["price"]),("currencyId","RUR"),
                    ("categoryId",mapping[x["category"]])):
            ET.SubElement(o,k).text=v
        for pic in x["photos"]:
            ET.SubElement(o,"picture").text=pic
        ET.SubElement(o,"name").text=x["name"]
        ET.SubElement(o,"vendorCode").text=x["sku"]
        ET.SubElement(o,"description").text=x["description"]
        for key,val in x["params"].items():
            if not CONTACT.search(key+" "+val):
                ET.SubElement(o,"param",{"name":key}).text=val
    ET.indent(root,space="  ")
    return ET.tostring(root,encoding="utf-8",xml_declaration=True)

def validate(payload):
    root=ET.fromstring(payload)
    offers=root.findall("./shop/offers/offer")
    ids={x.get("id") for x in offers}
    if len(offers)<MIN_OFFERS or len(ids)!=len(offers):
        raise RuntimeError("Too few offers or duplicate SKU IDs")
    for x in offers:
        if not x.findtext("name") or not x.findtext("vendorCode") or not x.findall("picture"):
            raise RuntimeError("Incomplete SKU "+str(x.get("id")))
        if CONTACT.search(x.findtext("description") or ""):
            raise RuntimeError("Supplier advertising in description "+str(x.get("id")))
        if "?offer=" not in (x.findtext("url") or ""):
            raise RuntimeError("Unstable offer link "+str(x.get("id")))
    if OUTPUT.exists():
        old=ET.parse(OUTPUT).getroot().findall("./shop/offers/offer")
        old_ids={x.get("id") for x in old}
        missing=old_ids-ids
        if old_ids and (len(ids)<len(old_ids)*.88 or len(missing)>max(20,len(old_ids)*.15)):
            raise RuntimeError("Unsafe SKU shrink; preserve current feed")
    return len(offers),sum(len(x.findall("picture")) for x in offers)

def main():
    models=discover_models()
    offers={}
    failures=[]
    # Fetch each model once, enumerate ALL possible variants, including colors.
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as executor:
        jobs={executor.submit(get,base):base for base in models}
        for i,future in enumerate(cf.as_completed(jobs),1):
            base=jobs[future]
            try:
                html=future.result()
                refs=variant_links(html,base)
                m=re.search(r'data-fl-item-id="(\d+)"',html)
                selected=m.group(1) if m else None
                for oid,details in refs.items():
                    if oid in offers and offers[oid][0]!=base:
                        raise RuntimeError("Offer ID collision for "+oid)
                    offers[oid]=(base,details,html if selected==oid else None)
            except Exception as exc:
                failures.append(base+" "+str(exc))
            if i%25==0:print("METTA_MODELS %d/%d offers=%d failures=%d" % (i,len(models),len(offers),len(failures)),flush=True)
    if failures:
        for err in failures[:15]:print("METTA_FAIL "+err,flush=True)
        raise RuntimeError("Model enumeration failed: "+str(len(failures)))
    products=[]
    # Each selected variant needs its own HTML for accurate current price and
    # variant-specific image. Reuse the first model fetch when it is exact.
    def worker(entry):
        oid,(base,details,embedded)=entry
        html=embedded or get(offer_url(base,oid))
        all_known={oid:details}
        return parse_offer(html,base,oid,all_known)
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as executor:
        jobs={executor.submit(worker,entry):entry[0] for entry in offers.items()}
        for i,future in enumerate(cf.as_completed(jobs),1):
            oid=jobs[future]
            try: products.append(future.result())
            except Exception as exc:failures.append(oid+" "+str(exc))
            if i%30==0 or i==len(jobs):
                print("METTA_PROGRESS %d/%d finished=%d failures=%d" % (i,len(jobs),len(products),len(failures)),flush=True)
    if failures:
        for err in failures[:20]:print("METTA_FAIL "+err,flush=True)
        raise RuntimeError("Variant parse failure; current YML preserved: "+str(len(failures)))
    payload=build(products)
    total,pics=validate(payload)
    temp=OUTPUT.with_suffix(".yml.tmp")
    temp.write_bytes(payload)
    os.replace(temp,OUTPUT)
    print("METTA_READY models=%d offers=%d pictures=%d bytes=%d" % (len(models),total,pics,len(payload)),flush=True)

if __name__=="__main__":main()
