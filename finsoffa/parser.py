#!/usr/bin/env python3
"""Finsoffa public catalog -> weekly content and availability YML for YML Pro.

One offer per real product/colour/upholstery variant (as represented by its
separate /product/ URL). Uses site sitemap + ALL catalog pagination pages.
No public shop prices, fictional warehouse quantity or vendor advertising.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = "https://finsoffa.ru"
OUTPUT = Path(os.getenv("FINSOFFA_OUTPUT", "finsoffa/catalog-content.yml"))
WORKERS = max(1, min(6, int(os.getenv("FINSOFFA_WORKERS", "4"))))
MIN_OFFERS = int(os.getenv("FINSOFFA_MIN_OFFERS", "100"))
MAX_PAGES = 80
TLS = threading.local()
PRODUCT_RE = re.compile(r"^/product/[^/?#]+/?$", re.I)
PHOTO_RE = re.compile(r"^/(?:photos/upload|upload)/.+\.(?:jpe?g|png|webp)$", re.I)
UNWANTED = re.compile(r"(?i)finsoffa(?:\.ru)?|наш[аией]\s+(?:компани|фабрик|сайт|магазин)|"
                      r"дилер|поставщик|свяжитесь|звоните|"
                      r"https?://|www\.|[\w.+-]+@[\w.-]+\.[a-z]{2,}|"
                      r"(?:\+7|8\s*800)[\s()\d-]{8,}")
BAD_FIELD = re.compile(r"(?i)производител|бренд|поставщик|менеджер|контакт|сайт|"
                       r"телефон|адрес\s+магазина|условия|доставк|оплат|цена|"
                       r"скидк|магазин|возврат|реклам")

def clean(v: object) -> str:
    return re.sub(r"\s+", " ", str(v or "").replace("\xa0", " ")).strip()

def local(url: str) -> str:
    p = urlsplit(urljoin(ROOT, url or ""))
    if p.scheme != "https" or p.hostname not in ("finsoffa.ru", "www.finsoffa.ru"):
        return ""
    # Keep query only on catalog pagination; no tracking or redirects.
    query = p.query if p.path.rstrip("/") == "/catalog" else ""
    return urlunsplit(("https", "finsoffa.ru", p.path, query, ""))

def product_url(link: str) -> str:
    url = local(link)
    p = urlsplit(url)
    if not PRODUCT_RE.fullmatch(p.path):
        return ""
    return f"{ROOT}{p.path.rstrip('/')}/"

def http() -> requests.Session:
    if not hasattr(TLS, "http"):
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; CatalogContentSync/1.0)",
                          "Accept-Language": "ru-RU,ru;q=0.9"})
        retry = Retry(total=3, connect=3, read=3, status=3, backoff_factor=0.8,
                      status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"],
                      respect_retry_after_header=True)
        s.mount("https://", HTTPAdapter(max_retries=retry))
        TLS.http = s
    return TLS.http

def get(url: str) -> str:
    safe = local(url)
    if not safe:
        raise RuntimeError(f"External URL forbidden: {url}")
    r = http().get(safe, timeout=(12, 35))
    r.raise_for_status()
    if len(r.content) < 500:
        raise RuntimeError(f"Unexpectedly small site response: {safe}")
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text

def sitemap_products() -> set[str]:
    xml = ET.fromstring(get(ROOT + "/sitemap.xml"))
    result = {link for el in xml.iter() if el.tag.endswith("loc")
              if (link := product_url((el.text or "").strip()))}
    if len(result) < MIN_OFFERS:
        raise RuntimeError(f"Suspiciously small sitemap: {len(result)} products")
    return result

def listing_products(text: str) -> set[str]:
    soup = BeautifulSoup(text, "html.parser")
    return {p for a in soup.select('a[href^="/product/"]')
            if (p := product_url(a.get("href", "")))}

def catalog_products() -> set[str]:
    home = get(ROOT + "/catalog/")
    soup = BeautifulSoup(home, "html.parser")
    pages = [1]
    for a in soup.select(".paginations a[href]"):
        link = local(a.get("href", ""))
        if urlsplit(link).path.rstrip("/") != "/catalog":
            continue
        for value in parse_qs(urlsplit(link).query).get("page", []):
            if value.isdigit():
                pages.append(int(value))
    last = max(pages)
    if last > MAX_PAGES:
        raise RuntimeError(f"Unexpected catalog pagination {last}")
    urls = listing_products(home)
    if not urls:
        raise RuntimeError("No products on catalog first page")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(get, f"{ROOT}/catalog/?page={i}"): i for i in range(2, last + 1)}
        for future in as_completed(futures):
            page = futures[future]
            entries = listing_products(future.result())
            if not entries:
                raise RuntimeError(f"Empty catalog page {page}")
            urls.update(entries)
    print(f"FINSOFFA_CATALOG pages={last} unique_items={len(urls)}", flush=True)
    return urls

def discover() -> set[str]:
    site = sitemap_products()
    cat = catalog_products()
    total = site | cat
    if len(total) < MIN_OFFERS:
        raise RuntimeError("Catalog unexpectedly reduced")
    print(f"FINSOFFA_DISCOVERED sitemap={len(site)} listing={len(cat)} combined={len(total)}", flush=True)
    return total

def json_product(soup: BeautifulSoup) -> dict:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        nodes = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for n in nodes:
            if isinstance(n, dict) and "Product" in str(n.get("@type", "")):
                return n
    raise RuntimeError("Product structured JSON missing")

def spec_fields(soup: BeautifulSoup) -> dict[str, str]:
    params: dict[str, str] = {}
    for el in soup.select(".fs-tabs__pane[data-pane='chars'] .fs-pd__char"):
        key = el.find("span")
        val = el.find("b")
        if not key or not val:
            continue
        k, v = clean(key.get_text(" ", strip=True)).rstrip(":"), clean(val.get_text(" ", strip=True))
        if not k or not v or len(k)>100 or len(v)>600 or BAD_FIELD.search(k) or UNWANTED.search(k+" "+v):
            continue
        if k.lower()=="артикул":
            continue
        params.setdefault(k, v)
    for el in soup.select(".fs-pd__quick-row"):
        key, val = el.select_one(".fs-pd__quick-k"), el.select_one(".fs-pd__quick-v")
        if key and val:
            k, v = clean(key.get_text(" ",strip=True)), clean(val.get_text(" ",strip=True))
            if k and v and not BAD_FIELD.search(k) and not UNWANTED.search(k+" "+v):
                params.setdefault(k,v)
    return params

def get_images(structured: dict, soup: BeautifulSoup) -> list[str]:
    images = structured.get("image") or []
    if isinstance(images, str):
        images=[images]
    if isinstance(images, dict):
        images=[images.get("url") or images.get("contentUrl") or ""]
    for b in soup.select(".fs-pd__gallery .fs-pd__thumb[data-src]"):
        images.append(b.get("data-src", ""))
    result=[]
    for raw in images:
        if not isinstance(raw, str):
            continue
        pic=local(raw)
        if PHOTO_RE.fullmatch(urlsplit(pic).path) and pic not in result:
            result.append(pic)
    return result[:40]

def categories(soup: BeautifulSoup) -> list[str]:
    names=[clean(el.get_text(" ",strip=True)) for el in soup.select(".breadcrumb__item")]
    names=[v for v in names if v and v.casefold() not in ("главная","каталог")]
    if names:
        names.pop()  # Last is the product name
    return names or ["Мебель и аксессуары"]

def presence(soup: BeautifulSoup, structured: dict) -> tuple[str,str]:
    el = soup.select_one(".fs-pd__stock")
    status = clean(el.get_text(" ", strip=True)).lower() if el else ""
    offer = structured.get("offers", {})
    if isinstance(offer, list):
        offer=offer[0] if offer else {}
    schema = clean(offer.get("availability","")).rsplit("/",1)[-1].casefold() if isinstance(offer,dict) else ""
    if re.search(r"в\s+налич|на\s+склад", status):
        if schema and schema not in ("instock","limitedavailability"):
            raise RuntimeError(f"Contradictory stock: {status} versus {schema}")
        return "true", "В наличии"
    if re.search(r"под\s+заказ|предзаказ|изготовлени",status):
        return "false", "Под заказ"
    if re.search(r"нет\s+в\s+налич|отсутств|распродан|снят\s+с\s+производ",status):
        return "false", "Нет в наличии"
    if schema in ("backorder","preorder","outofstock","soldout","discontinued"):
        return "false", "Под заказ" if schema in ("backorder","preorder") else "Нет в наличии"
    if schema=="instock":
        return "true", "В наличии"
    raise RuntimeError(f"Unrecognized availability: {status!r} {schema!r}")

def parse(url: str) -> dict:
    soup=BeautifulSoup(get(url),"html.parser")
    data=json_product(soup)
    name=clean(data.get("name") or "")
    sku=clean(data.get("sku") or "")
    heading=soup.select_one(".fs-pd__title")
    if heading:
        h_name=clean(heading.get_text(" ",strip=True))
        if h_name and name and h_name!=name:
            raise RuntimeError(f"Contradictory name {url}")
    article=soup.select_one(".fs-pd__art")
    if article:
        h_sku=clean(re.sub(r"(?i)^артикул\s*:\s*","",article.get_text(" ",strip=True)))
        if sku and h_sku and sku!=h_sku:
            raise RuntimeError(f"Contradictory article {url}: {sku} vs {h_sku}")
        sku=sku or h_sku
    if not name or not sku or len(sku)>100 or UNWANTED.search(name+" "+sku):
        raise RuntimeError(f"Invalid product name/article: {url}")
    photos=get_images(data,soup)
    if not photos:
        raise RuntimeError(f"Missing photos for {sku}")
    props=spec_fields(soup)
    stock,label=presence(soup,data)
    # Factual description only, no supplier advertising or contacts.
    cleanname=re.sub(r"(?i)\s+арт\.\s*"+re.escape(sku)+r"\s*$","",name).strip()
    description=clean(f"{cleanname}. " + " ".join(f"{k}: {v}." for k,v in props.items()))
    uid="finsoffa-"+sku  # Model/color-specific original vendor article is stable.
    return {
        "id": uid, "sku":sku, "name":cleanname, "photos":photos,
        "params":props, "category":categories(soup),
        "available":stock, "stock_label":label, "description":description
    }

def to_xml(items: list[dict]) -> bytes:
    root=ET.Element("yml_catalog", date=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    shop=ET.SubElement(root,"shop")
    ET.SubElement(shop,"name").text="Мегаполис"
    ET.SubElement(shop,"company").text="Мегаполис"
    ET.SubElement(shop,"url").text="https://profikompany.ru/"
    cats=ET.SubElement(shop,"categories")
    unique=set()
    for i in items:
        path=[]
        for name in i["category"]:
            path.append(name)
            unique.add(tuple(path))
    mapping={}
    for path in sorted(unique, key=lambda p:(len(p),p)):
        key="/".join(path).encode()
        number=str(1000000000+int(hashlib.sha256(key).hexdigest()[:15],16)%8000000000)
        if number in mapping.values():
            raise RuntimeError(f"Category ID collision {path}")
        mapping[path]=number
        attributes={"id":number}
        if len(path)>1:
            attributes["parentId"]=mapping[path[:-1]]
        ET.SubElement(cats,"category",attributes).text=path[-1]
    offers=ET.SubElement(shop,"offers")
    for item in sorted(items,key=lambda x:x["sku"]):
        o=ET.SubElement(offers,"offer",id=item["id"],available=item["available"])
        ET.SubElement(o,"name").text=item["name"]
        ET.SubElement(o,"categoryId").text=mapping[tuple(item["category"])]
        ET.SubElement(o,"vendorCode").text=item["sku"]
        ET.SubElement(o,"model").text=item["sku"]
        for pic in item["photos"]:
            ET.SubElement(o,"picture").text=pic
        ET.SubElement(o,"description").text=item["description"]
        for k,v in sorted(item["params"].items()):
            ET.SubElement(o,"param",name=k).text=v
        ET.SubElement(o,"param",name="Наличие").text=item["stock_label"]
    ET.indent(root,space="  ")
    return ET.tostring(root,encoding="utf-8",xml_declaration=True)

def verify(content: bytes, old: Path | None=None) -> tuple[int,int,int,int]:
    root=ET.fromstring(content)
    offers=root.findall("./shop/offers/offer")
    if len(offers)<MIN_OFFERS:
        raise RuntimeError(f"Too few products: {len(offers)}")
    ids=[p.get("id") for p in offers]
    codes=[p.findtext("vendorCode") for p in offers]
    if len(ids)!=len(set(ids)) or len(codes)!=len(set(codes)):
        raise RuntimeError("Duplicate IDs/vendor articles")
    if any(not p.findall("picture") for p in offers):
        raise RuntimeError("Product with no images")
    for p in offers:
        if p.get("available") not in ("true","false"):
            raise RuntimeError("Unknown stock status")
        for tag in ("price","quantity","vendor","url"):
            if p.find(tag) is not None:
                raise RuntimeError(f"Forbidden product field {tag}")
        if len(p.findall("./param[@name='Наличие']"))!=1:
            raise RuntimeError("Missing stock label")
        for field in ("description","name"):
            if UNWANTED.search(p.findtext(field) or ""):
                raise RuntimeError(f"Supplier data in {field}")
    if old and old.exists():
        previous=len(ET.parse(old).getroot().findall("./shop/offers/offer"))
        if previous>=MIN_OFFERS and len(offers)<previous*0.8:
            raise RuntimeError(f"Unsafe shrink {previous} -> {len(offers)}")
    available=sum(p.get("available")=="true" for p in offers)
    return len(offers),sum(len(p.findall("picture")) for p in offers),available,len(offers)-available

def main() -> None:
    urls=discover()
    result=[]
    errors=[]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures={pool.submit(parse,u):u for u in urls}
        for idx,f in enumerate(as_completed(futures),1):
            try:
                result.append(f.result())
            except Exception as exc:
                errors.append(f"{futures[f]}: {exc}")
            if idx%40==0:
                print(f"FINSOFFA_PROGRESS {idx}/{len(futures)} errors={len(errors)}",flush=True)
    if errors:
        raise RuntimeError(f"Incomplete catalog, prior YML preserved: count={len(errors)} errors={errors[:12]}")
    xml=to_xml(result)
    count,pictures,yes,no=verify(xml,OUTPUT)
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=OUTPUT.parent,delete=False,suffix=".tmp") as f:
        f.write(xml)
        temporary=Path(f.name)
    temporary.replace(OUTPUT)
    print(f"FINSOFFA_SUCCESS offers={count} pictures={pictures} available={yes} unavailable={no}",flush=True)

if __name__=="__main__":
    main()
