#!/usr/bin/env python3
"""Avalon: tables and subframes, separate categories in one weekly YML Pro feed."""
import concurrent.futures
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

BASE = "https://www.avalonmebel.ru"
CATEGORY = BASE + "/catalog/podstole-dlya-stola/"
PREFIX = "/catalog/podstole-dlya-stola/"
TABLE_CATEGORY = BASE + "/catalog/stoly-dlya-kafe/"
TABLE_PREFIX = "/catalog/stoly-dlya-kafe/"
OUTPUT = Path(os.getenv("OUTPUT", "catalog.yml"))
WORKERS = max(1, min(10, int(os.getenv("WORKERS", "5"))))
MIN_PRODUCTS = int(os.getenv("MIN_PRODUCTS", "200"))
TABLE_MIN_PRODUCTS = int(os.getenv("TABLE_MIN_PRODUCTS", "100"))
CATEGORIES = (
    {"url": CATEGORY, "prefix": PREFIX, "id": "1",
     "name": "Подстолья для столов", "min_products": MIN_PRODUCTS},
    {"url": TABLE_CATEGORY, "prefix": TABLE_PREFIX, "id": "2",
     "name": "Столы для кафе", "min_products": TABLE_MIN_PRODUCTS},
)
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "40"))
_TLS = threading.local()
CONTACT = re.compile(
    r"(?i)(?:https?://|www\.|[\w.+%-]+@[\w.-]+\.[a-z]{2,}|"
    r"(?:\+7|8\s*\(?800\)?|8\s*\(?495\)?|8\s*\(?812\)?)\s*[\d() -]{8,}|"
    r"\b(?:авалон|avalon|позвоните|звоните|свяжитесь|напишите|"
    r"менеджер|нашей компании|нашего магазина|нашем сайте|"
    r"оформить заказ|оформление заказа|заказать звонок|"
    r"доставк[аиу]|оплат[аиу]|самовывоз|кредит|рассрочк[аиу]|"
    r"выгодной цене|оставьте заявку|воспользуйтесь|заполните форму|"
    r"контактн\w*|адрес\w*|телефон\w*|оптовым клиентам|"
    r"мы предлагаем|мы работаем|акци[яию]|скидк[аиу])\b)"
)
MONEY = re.compile(r"(\d[\d\s\xa0]*(?:[,.]\d{1,2})?)\s*(?:₽|руб)", re.I)
RESIZED = re.compile(r"^/upload/resize_cache/(iblock|uf)/([^/]+)/[^/]+/(.+\.(?:jpe?g|png|webp))$", re.I)
IMAGE = re.compile(r"^/upload/(iblock|uf)/.+\.(?:jpe?g|png|webp)$", re.I)


def compact(value):
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def sanitize(value):
    parts = re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z])|\n+", str(value or ""))
    kept = []
    for part in parts:
        s = compact(part)
        if s and not CONTACT.search(s) and s not in kept:
            kept.append(s)
    return compact(" ".join(kept))[:10000]


def client():
    if not hasattr(_TLS, "client"):
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; MegapolisYMLPro/1.0)",
                          "Accept-Language": "ru-RU,ru;q=0.9"})
        _TLS.client = s
    return _TLS.client


def fetch(url):
    if urlparse(url).hostname not in {"www.avalonmebel.ru", "avalonmebel.ru"}:
        raise RuntimeError("Unapproved hostname: "+url)
    last = None
    for n in range(3):
        try:
            response = client().get(url, timeout=TIMEOUT)
            response.raise_for_status()
            if len(response.content) < 7000:
                raise RuntimeError("Short website response")
            response.encoding = response.apparent_encoding or "utf-8"
            return response.text
        except Exception as exc:
            last = exc
            if n < 2:
                time.sleep(n+1)
    raise RuntimeError("Fetch failed: %s: %s" % (url, last))


def product_category(href):
    """Return the explicitly allowed Avalon category for a product URL."""
    parsed = urlparse(urljoin(BASE, href))
    if parsed.hostname not in {"www.avalonmebel.ru", "avalonmebel.ru"}:
        return None
    for category in CATEGORIES:
        if parsed.path.startswith(category["prefix"]):
            suffix = parsed.path[len(category["prefix"]):].strip("/")
            if re.fullmatch("[A-Za-z0-9_-]{2,125}", suffix):
                return category
    return None


def product_url(href):
    category = product_category(href)
    if not category:
        return None
    suffix = urlparse(urljoin(BASE, href)).path[len(category["prefix"]):].strip("/")
    return category["url"] + suffix


def listing(html, category=None):
    category = category or CATEGORIES[0]
    soup = BeautifulSoup(html, "html.parser")
    grid = soup.select_one("#js-catalog-wrapper-id")
    if not grid:
        raise RuntimeError("Missing catalog grid in " + category["url"])
    total = int(grid.get("data-total") or 0)
    pages = int(grid.get("data-pages") or 0)
    urls = []
    for a in grid.select("a.product[href]"):
        url = product_url(a.get("href"))
        if url and product_category(url)["id"] == category["id"] and url not in urls:
            urls.append(url)
    if total < category["min_products"] or pages < 1 or pages > 80 or not urls:
        raise RuntimeError("Invalid grid for %s: total=%s pages=%s urls=%s" %
                           (category["url"], total, pages, len(urls)))
    return urls, total, pages


def discover_category(category):
    urls, expected, pages = listing(fetch(category["url"]), category)
    print("AVALON_DISCOVER category=%s expected=%d pages=%d" %
          (category["id"], expected, pages), flush=True)

    def read_page(n):
        links, total, pc = listing(
            fetch(category["url"] + "?PAGEN_1=" + str(n)), category)
        if total != expected or pc != pages:
            raise RuntimeError("Changing category totals: %s page %d" %
                               (category["url"], n))
        return n, links

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(WORKERS, 6)) as pool:
        results = list(pool.map(read_page, range(2, pages + 1)))
    for n, links in sorted(results):
        for link in links:
            if link not in urls:
                urls.append(link)
        print("AVALON_LIST category=%s page=%d/%d unique=%d" %
              (category["id"], n, pages, len(urls)), flush=True)
    if len(urls) != expected:
        raise RuntimeError("Catalog incomplete %s: %d URLs vs %d expected" %
                           (category["url"], len(urls), expected))
    return urls, expected


def discover():
    urls = []
    expected_total = 0
    for category in CATEGORIES:
        links, expected = discover_category(category)
        urls.extend(links)
        expected_total += expected
    if len(set(urls)) != expected_total:
        raise RuntimeError("Duplicate product URLs between categories")
    return urls, expected_total


def source_image(raw):
    parsed = urlparse(urljoin(BASE,raw))
    if parsed.hostname not in {"avalonmebel.ru","www.avalonmebel.ru"}:
        return None
    path = parsed.path
    m = RESIZED.fullmatch(path)
    if m:
        path = "/upload/"+m[1]+"/"+m[2]+"/"+m[3]
    if not IMAGE.fullmatch(path):
        return None
    return BASE + path


def pictures(soup):
    found=[]
    for sel in (".magnific-gallery__main",
                ".magnific-gallery__item a[href]",
                ".gallery-slides__item img[src]"):
        for node in soup.select(sel):
            image=source_image(node.get("href") or node.get("src") or "")
            if image and image not in found:
                found.append(image)

    # Dimension drawings are a separate gallery below the specifications,
    # not part of .magnific-gallery__item. Keep them LAST in the YML even
    # when there are more than 35 ordinary gallery photos.
    dimensions=[]
    for node in soup.select(".sku__chars .magnific-gallery__item1 a[href], "
                            ".sku__chars .magnific-gallery__item1 img[src]"):
        image=source_image(node.get("href") or node.get("src") or "")
        if image and image not in dimensions:
            dimensions.append(image)
    if dimensions:
        dimensions=dimensions[-35:]
        found=[image for image in found if image not in dimensions]
        return found[:35-len(dimensions)] + dimensions
    return found[:35]


def price_number(text):
    match=MONEY.search(compact(text))
    if not match:
        return None
    try:
        return float(match[1].replace("\xa0","").replace(" ","").replace(",","."))
    except ValueError:
        return None


def parse_product(html,url):
    if not product_url(url):
        raise RuntimeError("Outside allowed category")
    soup=BeautifulSoup(html,"html.parser")
    heading=soup.select_one("h1.sku__heading")
    name=compact(heading.get_text(" ",strip=True)) if heading else ""
    if not name or "404" in name or name.lower().startswith("ошибка"):
        raise RuntimeError("Missing product")
    pn=soup.select_one(".sku-info__top .sku__price")
    price=price_number(pn.get_text(" ",strip=True)) if pn else None
    params={}
    available=False
    for li in soup.select(".sku__chars .sku__info-list li"):
        span=li.select_one(".bold, b, strong, span")
        if not span: continue
        v=compact(span.get_text(" ",strip=True))
        raw=compact(li.get_text(" ",strip=True))
        k=compact(raw[:raw.rfind(v)]).strip(" :") if v in raw else ""
        if k.lower()=="наличие":
            available=v.lower()=="в наличии"
        elif k and v and not CONTACT.search(k) and not CONTACT.search(v):
            params[k]=v
    sku=params.pop("Артикул","") or urlparse(url).path.rstrip("/").split("/")[-1]
    caption=soup.select_one(".sku__caption")
    description=sanitize(caption.get_text("\n",strip=True) if caption else "")
    images=pictures(soup)
    if not images:
        raise RuntimeError("No source photos for "+url)
    return {"url":url,"name":name,"sku":sku,"price":price,
            "params":params,"description":description,
            "pictures":images,"available":available}


def offer_id(p):
    suffix=urlparse(p["url"]).path.rstrip("/").split("/")[-1]
    slug=re.sub("[^0-9A-Za-z_-]","",suffix)[:110]
    prefix = "avalon-table-" if product_category(p["url"])["id"] == "2" else "avalon-"
    return prefix + (slug or hashlib.sha256(p["url"].encode()).hexdigest()[:20])


def build_yml(products):
    root=ET.Element("yml_catalog",{"date":datetime.now().strftime("%Y-%m-%d %H:%M")})
    shop=ET.SubElement(root,"shop")
    ET.SubElement(shop,"name").text="Мегаполис - мебель для кафе"
    ET.SubElement(shop,"company").text="ООО Мегаполис"
    ET.SubElement(shop,"url").text="https://profikompany.ru/"
    currencies=ET.SubElement(shop,"currencies")
    ET.SubElement(currencies,"currency",{"id":"RUR","rate":"1"})
    categories=ET.SubElement(shop,"categories")
    for category in CATEGORIES:
        ET.SubElement(categories,"category",{"id":category["id"]}).text=category["name"]
    offers=ET.SubElement(shop,"offers")
    for p in sorted(products,key=lambda row:row["url"]):
        price=p["price"]
        offer=ET.SubElement(offers,"offer",{
            "id":offer_id(p),
            "available":"true" if p["available"] and price is not None and price>0 else "false"})
        ET.SubElement(offer,"url").text=p["url"]
        ET.SubElement(offer,"price").text=("%g" % price) if price is not None else "0"
        ET.SubElement(offer,"currencyId").text="RUR"
        ET.SubElement(offer,"categoryId").text=product_category(p["url"])["id"]
        for pic in p["pictures"]:
            ET.SubElement(offer,"picture").text=pic
        ET.SubElement(offer,"name").text=p["name"]
        ET.SubElement(offer,"vendorCode").text=p["sku"]
        if p["description"]:
            ET.SubElement(offer,"description").text=p["description"]
        for k,v in p["params"].items():
            ET.SubElement(offer,"param",{"name":k}).text=v
        if price is None or price<=0:
            ET.SubElement(offer,"param",{"name":"Цена по запросу"}).text="Да"
    ET.indent(root,space="  ")
    return ET.tostring(root,encoding="utf-8",xml_declaration=True)


def validate(blob,old_path=None):
    root=ET.fromstring(blob)
    offers=root.findall("./shop/offers/offer")
    ids=[o.get("id") for o in offers]
    if len(offers)<MIN_PRODUCTS or len(set(ids))!=len(ids):
        raise RuntimeError("YML invalid IDs or too few offers")
    counts = {category["id"]: 0 for category in CATEGORIES}
    defined = {cat.get("id") for cat in root.findall("./shop/categories/category")}
    for category in CATEGORIES:
        if category["id"] not in defined:
            raise RuntimeError("Missing YML category " + category["id"])
    for o in offers:
        category = product_category(o.findtext("url") or "")
        if not category:
            raise RuntimeError("Noncategory offer " + str(o.get("id")))
        if o.findtext("categoryId") != category["id"]:
            raise RuntimeError("Invalid categoryId for " + str(o.get("id")))
        counts[category["id"]] += 1
        if not o.findtext("name") or not o.findtext("vendorCode") or not o.findall("picture"):
            raise RuntimeError("Incomplete product " + str(o.get("id")))
        if CONTACT.search(o.findtext("description") or ""):
            raise RuntimeError("Supplier ad in YML")
    for category in CATEGORIES:
        if counts[category["id"]] < category["min_products"]:
            raise RuntimeError("Missing category products: %s has %d" %
                               (category["name"], counts[category["id"]]))
    if old_path and old_path.exists():
        old=ET.parse(old_path).getroot().findall("./shop/offers/offer")
        previous={x.get("id") for x in old}
        missing=previous-set(ids)
        if previous and (len(ids)<len(previous)*.90 or len(missing)>max(20,len(previous)*.15)):
            raise RuntimeError("Unsafe catalog shrink: %d SKUs missing" % len(missing))
    return {"offers":len(offers),"pictures":sum(len(o.findall("picture")) for o in offers), "categories": counts}


def main():
    urls,expected=discover()
    products=[]
    errors=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        future_to_url={pool.submit(fetch,url):url for url in urls}
        for i,future in enumerate(concurrent.futures.as_completed(future_to_url),1):
            url=future_to_url[future]
            try:
                products.append(parse_product(future.result(),url))
            except Exception as e:
                errors.append("%s: %s" % (url,e))
            if i%25==0 or i==len(urls):
                print("AVALON_PROGRESS %d/%d parsed=%d errors=%d" % (i,len(urls),len(products),len(errors)),flush=True)
    if errors:
        for error in errors[:20]: print("ERROR "+error,flush=True)
        raise RuntimeError("%d product pages failed, previous feed untouched" % len(errors))
    if len(products)!=expected:
        raise RuntimeError("Incomplete product count")
    data=build_yml(products)
    report=validate(data,OUTPUT)
    tmp=OUTPUT.with_suffix(".yml.tmp")
    tmp.write_bytes(data)
    os.replace(tmp,OUTPUT)
    print("AVALON_READY offers=%d images=%d bytes=%d" % (report["offers"],report["pictures"],len(data)),flush=True)


if __name__=="__main__":
    main()
