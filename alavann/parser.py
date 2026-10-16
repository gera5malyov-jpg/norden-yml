#!/usr/bin/env python3
"""Alavann YML Pro feed restricted to six bathroom furniture categories."""
from __future__ import annotations
import hashlib, json, os, re, sys, threading, xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag, parse_qs
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://alavann-shop.ru"
OUT = Path(os.getenv("ALAVANN_OUTPUT", "alavann/alavann.yml"))
WORKERS = min(12, max(1, int(os.getenv("ALAVANN_WORKERS", "6"))))
MINIMUM = int(os.getenv("ALAVANN_MIN_OFFERS", "140"))
_tls = threading.local()

def session():
    if not hasattr(_tls, "session"):
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; AlavannYML/1.0; website catalog parser)",
                          "Accept-Language": "ru-RU,ru;q=0.9"})
        retry = Retry(total=3, backoff_factor=1.2, status_forcelist=[429,500,502,503,504],
                      allowed_methods=["GET"], respect_retry_after_header=True)
        s.mount("https://", HTTPAdapter(max_retries=retry))
        _tls.session = s
    return _tls.session

def get(url):
    r = session().get(url, timeout=(15,40))
    r.raise_for_status()
    return r

def clean(x):
    return re.sub(r"\s+", " ", str(x or "")).strip()

def canonical(url):
    p = urlparse(urldefrag(urljoin(BASE, str(url or "")))[0])
    if p.scheme != "https" or p.netloc.lower() not in ("alavann-shop.ru","www.alavann-shop.ru"):
        return ""
    path = re.sub(r"/{2,}", "/", p.path)
    if not path.endswith("/") and "." not in path.split("/")[-1]:
        path += "/"
    return BASE + path

# ONLY these six category directories are approved for the YML feed.
# Prefix matching includes products under each directory, never sibling categories.
CATALOG_ROOTS = (
    "/mebel/cupboards/",
    "/mebel/komody/",
    "/mebel/zerkala-v-vannuyu/",
    "/mebel/stand/",
    "/mebel/set/",
    "/mebel/mirror-cupboard/",
)

def in_scope(url):
    normalized = canonical(url)
    if not normalized:
        return False
    path = urlparse(normalized).path
    return any(path.startswith(section) and path != section and
               "/filter/" not in path and not path.endswith(".xml")
               for section in CATALOG_ROOTS)

def sitemap_urls():
    queue = deque([BASE + "/sitemap.xml"])
    seen, urls = set(), set()
    while queue and len(seen) < 40:
        url = queue.popleft()
        if url in seen: continue
        seen.add(url)
        root = ET.fromstring(get(url).content)
        locs = [clean(e.text) for e in root.iter()
                if (e.tag == "loc" or e.tag.endswith("}loc")) and e.text]
        if root.tag.endswith("sitemapindex"):
            queue.extend(u for u in locs if u.startswith(BASE + "/") and u not in seen)
        elif root.tag.endswith("urlset"):
            urls.update(u for original in locs if (u := canonical(original))
                        and in_scope(u))
    if len(urls) < 100:
        raise RuntimeError(f"Unsafe scoped sitemap: only {len(urls)} URL candidates")
    return sorted(urls)

# The vendor's sitemap is stale. Traverse all pages of each allowed category
# in addition to category-scoped sitemap links.
def listing_page_count(soup, section_url):
    """Find the last numbered page, including the site's last-page shortcut."""
    end = urlparse(section_url).path.rstrip("/") + "/"
    pages = [1]
    for a in soup.select("a[href]"):
        p = urlparse(urljoin(section_url,a.get("href","")))
        if p.netloc not in ("alavann-shop.ru","www.alavann-shop.ru"):
            continue
        if p.path.rstrip("/") + "/" != end:
            continue
        raw = parse_qs(p.query).get("page",[])
        if raw and raw[0].isdigit():
            pages.append(int(raw[0]))
    count = max(pages)
    if count > 250:
        raise RuntimeError(f"Suspiciously large page count: {section_url} {count}")
    return count

def listing_product_links(soup):
    return {u for a in soup.select("a.product__name[href]")
            if (u := canonical(a.get("href"))) and in_scope(u)}

def catalog_urls():
    """Enumerate all live products, not just old sitemap entries.

    Root category pages contain the full product inventory, including fresh
    product cards absent from sitemap.xml. Use every pagination page.
    Fail the run (retain old YML) if any page cannot be loaded.
    """
    all_pages = []
    all_urls = set()
    for section in CATALOG_ROOTS:
        root = BASE + section
        soup = BeautifulSoup(get(root).content,"lxml")
        count = listing_page_count(soup,root)
        first = listing_product_links(soup)
        if not first:
            raise RuntimeError(f"Empty scoped catalog page: {root}")
        all_urls.update(first)
        all_pages.extend(f"{root}?page={n}" for n in range(2,count+1))
        print(f"Listing {section}: {count} pages, page 1: {len(first)} products",flush=True)
    errors = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(get,url):url for url in all_pages}
        for i,future in enumerate(as_completed(futures),1):
            url = futures[future]
            try:
                soup = BeautifulSoup(future.result().content,"lxml")
                links = listing_product_links(soup)
                if not links:
                    errors.append(f"Empty catalog page {url}")
                all_urls.update(links)
            except Exception as exc:
                errors.append(f"{url}: {exc}")
            if i%30==0:
                print(f"Catalog pages {i}/{len(all_pages)}, live URLs={len(all_urls)}",flush=True)
    if errors:
        raise RuntimeError(f"Incomplete catalog listings: {errors[:8]}")
    if len(all_urls)<140:
        raise RuntimeError(f"Unsafe scoped live catalog: only {len(all_urls)} URLs")
    print(f"Live catalog: {len(all_urls)} products from {len(all_pages)+len(CATALOG_ROOTS)} pages",flush=True)
    return all_urls

def objects(soup):
    def walk(o):
        if isinstance(o, dict):
            yield o
            for value in o.values(): yield from walk(value)
        elif isinstance(o, list):
            for value in o: yield from walk(value)
    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        try: yield from walk(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError): continue

def typed(obj, typ):
    types = obj.get("@type") or []
    return any(str(t).rsplit("/",1)[-1].lower() == typ.lower()
               for t in ([types] if isinstance(types,str) else types))

def product_object(soup):
    return next((obj for obj in objects(soup) if typed(obj,"Product")), {})

def breadcrumb_categories(soup):
    data = next((obj for obj in objects(soup) if typed(obj,"BreadcrumbList")), {})
    found = []
    for el in data.get("itemListElement", []):
        item = el.get("item", {}) if isinstance(el,dict) else {}
        if isinstance(item,dict):
            url = canonical(item.get("@id") or item.get("url"))
            name = clean(item.get("name"))
            if url and name and urlparse(url).path != "/":
                found.append((url,name))
    return found[:-1] if len(found)>1 else []

def specs(soup):
    result = {}
    for item in soup.select("#card-1 .definitions-item"):
        key = item.select_one(".definitions-item__key")
        value = item.select_one(".definitions-item__value")
        if key and value:
            k = clean(key.get_text(" ",strip=True)).rstrip(":")
            v = clean(value.get_text(" ",strip=True))
            if k and v and len(k)<=120 and len(v)<=1500: result.setdefault(k,v)
    return result

def images(soup, product):
    result=[]
    for a in soup.select("form[data-card-form] a[data-fancybox='gallery-slider'], .card-slider a[data-fancybox='gallery-slider']"):
        url=canonical(a.get("href"))
        if "/upload/" in url and url not in result: result.append(url)
    if not result:
        values=product.get("image") or []
        if isinstance(values,(str,dict)): values=[values]
        for val in values:
            url=canonical(val.get("url") if isinstance(val,dict) else val)
            if "/upload/" in url and url not in result: result.append(url)
    return result[:35]

def price(value):
    if isinstance(value,(float,int)) and value > 0:
        return ("%.2f" % value).rstrip("0").rstrip(".")
    s=str(value or "").replace(" ","").replace("\xa0","").replace(",",".")
    return s if re.fullmatch(r"\d+(?:\.\d{1,2})?",s) and float(s)>0 else ""

def parse(url):
    response=get(url)
    if "text/html" not in response.headers.get("Content-Type","text/html"): return None
    soup=BeautifulSoup(response.content,"lxml")
    product=product_object(soup)
    if not product: return None
    offer=product.get("offers") or {}
    if isinstance(offer,list): offer=next((a for a in offer if isinstance(a,dict)),{})
    if not isinstance(offer,dict): offer={}
    sku=clean(product.get("sku") or offer.get("sku"))
    name=clean(product.get("name"))
    amount=price(offer.get("price"))
    if not sku or not name or not amount:
        raise ValueError(f"Missing sku/name/price: {url}")
    if not re.fullmatch(r"[\w./-]+",sku,re.UNICODE):
        raise ValueError(f"Invalid article {sku}")
    state=clean(offer.get("availability")).lower()
    if state:
        available=state.rsplit("/",1)[-1] in ("instock","limitedavailability","onlineonly","preorder")
    else:
        tag=soup.select_one("[data-card-status]")
        available=bool(tag and "налич" in tag.get_text(" ",strip=True).lower())
    props=specs(soup)
    desc=clean(product.get("description"))
    if not desc:
        block=soup.select_one("#card-2 .card-body") or soup.select_one("#card-2")
        desc=clean(block.get_text(" ",strip=True)) if block else ""
    trail=breadcrumb_categories(soup)
    if not trail:
        first=urlparse(url).path.strip("/").split("/",1)[0]
        trail=[(BASE+"/"+first+"/",{"mebel":"Мебель для ванной","screens":"Экраны под ванну"}.get(first,first.capitalize()))]
    return {"sku":sku,"name":name,"url":url,"price":amount,"available":available,
            "vendor":props.get("Производитель") or "Alavann",
            "images":images(soup,product),"description":desc,"params":props,"trail":trail}

def category_id(url):
    return str(int(hashlib.sha256(url.encode()).hexdigest()[:12],16) % 2000000000 + 1)

def feed(products):
    root=ET.Element("yml_catalog",date=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    shop=ET.SubElement(root,"shop")
    for k,v in [("name","Alavann"),("company","Alavann"),("url",BASE)]:
        ET.SubElement(shop,k).text=v
    ET.SubElement(ET.SubElement(shop,"currencies"),"currency",id="RUB",rate="1")
    categories={}
    for p in products:
        parent=None
        for url,name in p["trail"]:
            cid=category_id(url)
            if cid in categories and categories[cid][0]!=url:
                raise RuntimeError("Category ID hash collision")
            categories[cid]=(url,name,parent)
            parent=cid
        p["category"]=parent
    croot=ET.SubElement(shop,"categories")
    for cid,(url,name,parent) in sorted(categories.items(),key=lambda pair:pair[1][0]):
        attrib={"id":cid}
        if parent: attrib["parentId"]=parent
        ET.SubElement(croot,"category",attrib).text=name
    offers=ET.SubElement(shop,"offers")
    for p in sorted(products,key=lambda item:item["sku"]):
        el=ET.SubElement(offers,"offer",id=p["sku"],available=str(p["available"]).lower())
        fields=[("categoryId",p["category"]),("url",p["url"]),("name",p["name"]),
                ("vendor",p["vendor"]),("vendorCode",p["sku"]),("model",p["sku"]),
                ("price",p["price"]),("currencyId","RUB")]
        for key,val in fields:
            if val: ET.SubElement(el,key).text=str(val)
        for url in p["images"]:
            ET.SubElement(el,"picture").text=url
        if p["description"]:
            ET.SubElement(el,"description").text=p["description"]
        for name,val in p["params"].items():
            ET.SubElement(el,"param",name=name).text=val
    ET.indent(root,space="  ")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n'+ET.tostring(root,encoding="utf-8")+b"\n"

def main():
    mapped=set(sitemap_urls())
    current=catalog_urls()
    urls=sorted(url for url in mapped | current if in_scope(url))
    print(f"Sitemap candidates: {len(mapped)}; live catalog: {len(current)}; "
          f"newly discovered: {len(current-mapped)}; combined: {len(urls)}",flush=True)
    products={}
    errors=[]
    duplicates=0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures={pool.submit(parse,url):url for url in urls}
        for i,future in enumerate(as_completed(futures),1):
            try:
                item=future.result()
                if item:
                    existing=products.get(item["sku"])
                    if existing:
                        duplicates+=1
                        if ((item["available"] and not existing["available"])
                            or len(item["images"])>len(existing["images"])):
                            products[item["sku"]]=item
                    else: products[item["sku"]]=item
            except requests.HTTPError as exc:
                if exc.response is None or exc.response.status_code not in (404,410):
                    errors.append(str(exc)+" "+futures[future])
            except Exception as exc:
                errors.append(str(exc)+" "+futures[future])
            if i%100==0:
                print(f"{i}/{len(urls)} pages; {len(products)} products; {len(errors)} errors",flush=True)
    print(f"Products={len(products)}, duplicates={duplicates}, errors={len(errors)}",flush=True)
    for err in errors[:20]: print("ERROR",err,file=sys.stderr)
    if len(products)<MINIMUM or len(errors)>max(35,len(urls)*0.1):
        raise RuntimeError("Incomplete crawl; preserving existing catalog")
    if OUT.exists():
        # An intentional narrowing of the feed removes unrelated categories.
        # Compare safety thresholds against old in-scope SKUs only, never the
        # previous entire-site catalog.
        try:
            old_offers=ET.parse(OUT).getroot().findall("./shop/offers/offer")
        except (ET.ParseError,OSError):
            old_offers=[]
        previous={el.get("id") for el in old_offers
                  if in_scope(el.findtext("url") or "")}
        if previous and len(products)<len(previous)*0.8:
            raise RuntimeError(
                f"Unsafe scoped catalog shrink {len(previous)} -> {len(products)}")
        missing=previous-products.keys()
        if previous and len(missing)>len(previous)*0.2:
            raise RuntimeError(
                f"Unsafe scoped SKU churn: {len(missing)}/{len(previous)} disappeared")
    excluded=[p["url"] for p in products.values() if not in_scope(p["url"])]
    if excluded:
        raise RuntimeError(f"Out-of-scope products rejected: {excluded[:10]}")
    result=feed(list(products.values()))
    ET.fromstring(result)
    OUT.parent.mkdir(parents=True,exist_ok=True)
    temp=OUT.with_suffix(".yml.tmp")
    temp.write_bytes(result)
    temp.replace(OUT)
    print(f"Published {len(products)} products to {OUT} ({len(result)} bytes)",flush=True)

if __name__=="__main__":
    main()
