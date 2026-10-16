#!/usr/bin/env python3
"""DECcon public product pages -> content-only YML for Webasyst YML Pro.

No price, stock, quantity, availability or source-store branding is published.
Images are ORIGINAL public /upload/iblock or /upload/uf files, not compressed
thumbnails. Existing baked-in watermarks are not edited or removed.
"""
from __future__ import annotations
import os, re, tempfile, threading
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

BASE = "https://deccon.ru"
OUT = Path(os.getenv("DECCON_OUTPUT", "deccon/catalog-content.yml"))
WORKERS = max(1, min(7, int(os.getenv("DECCON_WORKERS", "5"))))
MIN_OFFERS = int(os.getenv("DECCON_MIN_OFFERS", "150"))
MAX_PAGES = 120
_tls = threading.local()
DETAIL_PATH = re.compile(r"^/catalog/(?:[a-z0-9_-]+/)+(\d+)/?$", re.I)
ORIGINAL_IMAGE_PATH = re.compile(r"^/upload/(?:iblock|uf)/.+\.(?:jpe?g|png|webp)$", re.I)
BANNED = re.compile(
    r"(?i)декор\s*концепт|d[eé]cor\s*concept|deccon(?:\.ru)?|"
    r"звоните|закажите|наш[аиие]\s+(?:компани|фабрик|менеджер|сайт|магазин)|"
    r"поставщик|дилер|наш\s+офис|интернет.магазин|"
    r"(?:\+7|8\s?800)\s*[\d\s()+-]{8,}|"
    r"[\w.+-]+@[\w.-]+\.[a-z]{2,}|https?://|www\."
)
BAD_SPEC = re.compile(r"(?i)производител|контакт|адрес|телефон|сайт|продавец|менеджер|доставк|оплат|гарантийн|стаж|под заказ")
MAIN_CATEGORIES = {
    "chasy_napolnye_mekhanicheskie": "Часы напольные механические",
    "chasy_napolnye_kvartsevye": "Часы напольные кварцевые",
    "nedorogie_napolnye_chasy": "Часы напольные",
    "sovremennye_napolnye_chasy": "Современные напольные часы",
    "nastennye_chasy_s_kukushkoy": "Часы с кукушкой",
    "nastennye_s_mayatnikom": "Настенные часы с маятником",
    "nastennye_chasy_s_boem": "Настенные часы с боем",
    "interernye_nastennye_chasy": "Настенные часы",
    "nastolnye_mekhanicheskie_chasy": "Настольные механические часы",
    "nastolnye_chasy_kvartsevye": "Настольные кварцевые часы",
    "chasy_po_aktsii": "Часы",
}

def clean(x) -> str:
    return re.sub(r"\s+", " ", str(x or "").replace("\xa0", " ")).strip()

def http() -> requests.Session:
    if not hasattr(_tls, "session"):
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; MegapolisProductCatalog/1.0)",
                          "Accept-Language": "ru-RU,ru;q=0.9"})
        retry = Retry(total=3, backoff_factor=1.0, status_forcelist=[429,500,502,503,504],
                      allowed_methods=["GET"], respect_retry_after_header=True)
        s.mount("https://", HTTPAdapter(max_retries=retry))
        _tls.session = s
    return _tls.session

def canonical(url: str) -> str:
    v = urlsplit(urljoin(BASE, str(url or "")))
    if v.scheme != "https" or v.hostname not in ("deccon.ru", "www.deccon.ru"):
        return ""
    return urlunsplit(("https", "deccon.ru", re.sub("/{2,}", "/", v.path), v.query, ""))

def fetch(url: str) -> str:
    safe = canonical(url)
    if not safe:
        raise ValueError(f"External URL blocked: {url}")
    response = http().get(safe, timeout=(15,40))
    response.raise_for_status()
    if len(response.content) < 2000:
        raise RuntimeError(f"Unexpectedly small response: {safe}")
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text

def canonical_product(href: str) -> str:
    v = canonical(href)
    m = DETAIL_PATH.fullmatch(urlsplit(v).path)
    if not m:
        return ""
    return f"{BASE}{urlsplit(v).path.rstrip('/')}/"

def collect_listing(html: str) -> set[str]:
    soup = BeautifulSoup(html, "html.parser")
    return {p for a in soup.select(".new_catalog_list_item a[href]")
            if (p := canonical_product(a.get("href","")))}

def listing_page_numbers(html: str) -> tuple[str, list[int]]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: dict[str, list[int]] = {}
    for a in soup.select(".module-pagination a[href]"):
        url = canonical(a.get("href",""))
        v = urlsplit(url)
        if v.path.rstrip("/") != "/catalog":
            continue
        for k, values in parse_qs(v.query).items():
            if re.fullmatch(r"PAGEN_\d+",k) and all(i.isdigit() for i in values):
                candidates.setdefault(k,[]).extend(int(i) for i in values)
    if not candidates:
        raise RuntimeError("Could not identify catalog pagination")
    key, pages = max(candidates.items(), key=lambda e:max(e[1]))
    last=max(pages)
    if last <= 0 or last > MAX_PAGES:
        raise RuntimeError(f"Unsafe page count {last}")
    return key,list(range(1,last+1))

def discover() -> set[str]:
    home = fetch(BASE+"/catalog/")
    key, pages = listing_page_numbers(home)
    found = collect_listing(home)
    if not found:
        raise RuntimeError("No listing items found on first page")
    failures=[]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        tasks={pool.submit(fetch,f"{BASE}/catalog/?{key}={n}"): n for n in pages[1:]}
        for fut in as_completed(tasks):
            n=tasks[fut]
            try:
                urls=collect_listing(fut.result())
                if not urls:
                    raise RuntimeError(f"Empty listing page {n}")
                found.update(urls)
            except Exception as exc:
                failures.append(f"Page {n}: {exc}")
    if failures:
        raise RuntimeError(f"Incomplete catalog: {failures[:10]}")
    if len(found) < MIN_OFFERS:
        raise RuntimeError(f"Only {len(found)} distinct products; expected >= {MIN_OFFERS}")
    print(f"DECCON_DISCOVERED pages={len(pages)} products={len(found)}",flush=True)
    return found

def category(url: str) -> tuple[str,str]:
    parts=urlsplit(url).path.strip("/").split("/")
    section=parts[1] if len(parts)>2 else ""
    return section, MAIN_CATEGORIES.get(section, "Часы")

def sanitized_name(s: str) -> str:
    name = clean(re.sub(r"(?i)\bдекор\s*концепт\b|\bdecor\s*concept\b", "",s))
    return name

def specs(soup: BeautifulSoup) -> dict[str,str]:
    found={}
    for li in soup.select(".new_product_param_2 li"):
        strong=li.find("strong")
        if not strong:
            continue
        value=clean(strong.get_text(" ",strip=True))
        label=clean(li.get_text(" ",strip=True).split(" - ",1)[0]).rstrip(" -:")
        if label and value and len(label)<90 and len(value)<400 and not BAD_SPEC.search(label) and not BANNED.search(value):
            found.setdefault(label,value)
    # Other actual product technical tables, when available.
    for tr in soup.select(".new_product_tabs .detail_text table tr"):
        cells=tr.find_all(["td","th"],recursive=False)
        if len(cells)!=2:
            continue
        label=clean(cells[0].get_text(" ",strip=True)).rstrip(":")
        value=clean(cells[1].get_text(" ",strip=True))
        if (label and value and len(label)<90 and len(value)<400 and
                not BAD_SPEC.search(label) and not BANNED.search(value)):
            found.setdefault(label,value)
    return found

def images(soup: BeautifulSoup) -> list[str]:
    links=[]
    # Anchor href is the genuine original image, while nested img is resized WebP.
    for a in soup.select(".new_product_img .slider-single a[data-lightbox='image'][href]"):
        raw=canonical(a.get("href",""))
        if ORIGINAL_IMAGE_PATH.fullmatch(urlsplit(raw).path) and raw not in links:
            links.append(raw)
    # The first original image may be represented only as product schema metadata.
    if not links:
        for tag in soup.select("meta[itemprop='image'][content]"):
            raw=canonical(tag.get("content",""))
            if ORIGINAL_IMAGE_PATH.fullmatch(urlsplit(raw).path) and raw not in links:
                links.append(raw)
    return links[:50]

def make_description(name: str, properties: dict[str,str]) -> str:
    # Description is rebuilt from structured factual fields; no company ads, slogans,
    # contacts, boilerplate, marketing assertions or copyright-heavy site prose.
    blocks=[f"{name}."]
    for k,v in properties.items():
        blocks.append(f"{k}: {v}.")
    return clean(" ".join(blocks))

def parse(url: str) -> dict:
    html=fetch(url)
    soup=BeautifulSoup(html,"html.parser")
    root=soup.select_one(".new_product")
    title=soup.select_one(".new_product_info h1")
    if not root or not title:
        raise RuntimeError(f"Missing actual product page markers: {url}")
    name=sanitized_name(title.get_text(" ",strip=True))
    article_el=soup.select_one("[data-module='zverushki_microm'] meta[itemprop='sku']")
    if not article_el:
        article_el=soup.select_one("meta[itemprop='sku']")
    sku=clean(article_el.get("content","")) if article_el else ""
    if BANNED.search(name) or BANNED.search(sku):
        raise RuntimeError(f"Supplier data in article/title: {url}")
    id_match=DETAIL_PATH.fullmatch(urlsplit(url).path)
    if not id_match:
        raise RuntimeError(f"Missing product ID in URL: {url}")
    source_id=id_match.group(1)
    props=specs(soup)
    photos=images(soup)
    if not name or not photos:
        raise RuntimeError(f"Missing name/images: {url}")
    return {
        "id": "deccon-"+source_id, "name": name, "sku": sku,
        "category": category(url), "photos": photos, "props": props,
        "description": make_description(name,props)
    }

def serialize(products: list[dict]) -> bytes:
    root=ET.Element("yml_catalog", date=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    shop=ET.SubElement(root,"shop")
    ET.SubElement(shop,"name").text="Мегаполис"
    ET.SubElement(shop,"company").text="Мегаполис"
    ET.SubElement(shop,"url").text="https://profikompany.ru/"
    categories=ET.SubElement(shop,"categories")
    unique=sorted({p["category"] for p in products},key=lambda x:x[0])
    mapping={c[0]:str(i+1) for i,c in enumerate(unique)}
    for slug,title in unique:
        ET.SubElement(categories,"category",id=mapping[slug]).text=title
    offers=ET.SubElement(shop,"offers")
    for p in sorted(products,key=lambda x:int(x["id"].split("-")[-1])):
        offer=ET.SubElement(offers,"offer",id=p["id"])
        ET.SubElement(offer,"name").text=p["name"]
        ET.SubElement(offer,"categoryId").text=mapping[p["category"][0]]
        if p["sku"]:
            ET.SubElement(offer,"vendorCode").text=p["sku"]
            ET.SubElement(offer,"model").text=p["sku"]
        for im in p["photos"]:
            ET.SubElement(offer,"picture").text=im
        ET.SubElement(offer,"description").text=p["description"]
        for key,val in sorted(p["props"].items()):
            ET.SubElement(offer,"param",name=key).text=val
        # No original product URL, source company, retailer prices, stock or vendor.
    ET.indent(root,space="  ")
    return ET.tostring(root,encoding="utf-8",xml_declaration=True)

def validate(data: bytes, previous: Path | None = None) -> tuple[int,int,int]:
    root=ET.fromstring(data)
    offers=root.findall("./shop/offers/offer")
    if len(offers)<MIN_OFFERS:
        raise RuntimeError(f"Too few offers: {len(offers)}")
    seen_ids=set()
    seen_skus=set()
    photos=0
    skus=0
    for o in offers:
        ident=o.get("id")
        code=clean(o.findtext("vendorCode"))
        if ident in seen_ids or not ident:
            raise RuntimeError(f"Duplicate ID {ident}")
        seen_ids.add(ident)
        if code:
            key=code.casefold()
            if key in seen_skus:
                raise RuntimeError(f"Duplicate article {code}")
            seen_skus.add(key)
            skus+=1
        imgs=o.findall("picture")
        if not imgs:
            raise RuntimeError(f"No photos: {ident}")
        photos+=len(imgs)
        for x in ("price","available","quantity","vendor","url"):
            if o.find(x) is not None:
                raise RuntimeError(f"Forbidden supplier/commerce field {x}: {ident}")
        for x in ("name","description"):
            if BANNED.search(o.findtext(x) or ""):
                raise RuntimeError(f"Supplier info in {x}: {ident}")
    if previous and previous.exists():
        existing=ET.parse(previous).getroot().findall("./shop/offers/offer")
        if len(existing)>=MIN_OFFERS and len(offers)<len(existing)*0.80:
            raise RuntimeError(f"Potentially incomplete catalog: {len(existing)} -> {len(offers)}")
    return len(offers),photos,skus

def main() -> None:
    links=discover()
    products=[]
    errors=[]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        tasks={pool.submit(parse,url):url for url in links}
        for i,fut in enumerate(as_completed(tasks),1):
            try:
                products.append(fut.result())
            except Exception as exc:
                errors.append(f"{tasks[fut]}: {exc}")
            if i%75 == 0:
                print(f"DECCON_PROGRESS {i}/{len(tasks)}, errors={len(errors)}",flush=True)
    if errors:
        raise RuntimeError(f"Incomplete products ({len(errors)}): {errors[:12]}")
    data=serialize(products)
    count,photos,skus=validate(data, OUT)
    if skus<count*0.75:
        raise RuntimeError(f"Unexpectedly missing article codes: {skus}/{count}")
    OUT.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=OUT.parent,delete=False,suffix=".tmp") as f:
        f.write(data)
        tmp=Path(f.name)
    os.replace(tmp,OUT)
    print(f"DECCON_SUCCESS offers={count} pictures={photos} vendor_codes={skus}",flush=True)

if __name__=="__main__":
    main()
