#!/usr/bin/env python3
"""Full championtool.ru product catalog -> content + availability YML Pro.

Website public pages only; no price is exported. This tool uses deterministic
sitemap partitioning and a GitHub Actions matrix to avoid a single long job.
"""
from __future__ import annotations
import argparse, gzip, hashlib, html, json, os, re, sys, threading, time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urljoin, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = "https://championtool.ru"
SITEMAP = ROOT + "/sitemap.xml"
TIMEOUT = (12, 35)
THREAD = threading.local()
WORKERS = max(1, min(5, int(os.getenv("CHAMPION_WORKERS", "3"))))
MIN_PRODUCTS = int(os.getenv("CHAMPION_MIN_OFFERS", "15000"))
MAX_SHRINK = float(os.getenv("CHAMPION_MAX_SHRINK", "0.20"))
BANNED = re.compile(
    r"(?i)https?://|www\.|[\w.+-]+@[\w.-]+\.[a-z]{2,}|"
    r"(?:(?:\+7|8\s*800)\s*[\d\s()+-]{8,})|"
    r"обратн\w+ звон|интернет-магазин|наш\w* магазин|"
    r"обращайтесь|звоните|позвоните|контакт\w+|"
    r"поставщик|официальный дилер|купите у нас"
)
SKIP_SPEC = re.compile(
    r"(?i)поставщик|контакты|телефон|заказ|платеж|доставк|"
    r"стоимост|цена|скидка|наличие|магазин|рейтинг|отзыв"
)
STOCK_OFF = re.compile(r"(?i)под заказ|нет в наличии|отсутств|снят[аоы]?\s+с\s+производ|распродан")
STOCK_POS = re.compile(r"(?i)в наличии|на складе|срок доставки\s*\d")
PHOTO = re.compile(r"^/media/(?:images|cache/product_popup|cache/product_thumb)/product/.+\.(?:jpe?g|png|webp)$", re.I)
_SKIPS = ("privacy", "oferta", "about", "news", "service", "help", "blog", "video", "otzyvy", "login", "register")

def clean(v: object) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(v or "")).replace("\xa0"," ")).strip()

def normalize(raw: str) -> str:
    p=urlsplit(urljoin(ROOT, raw or ""))
    if p.scheme!="https" or p.hostname not in ("championtool.ru","www.championtool.ru"):
        return ""
    return urlunsplit(("https","championtool.ru",p.path.rstrip("/"),"",""))

def http() -> requests.Session:
    if not hasattr(THREAD, "http"):
        s=requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0 (compatible; ProductContentSync/1.0)",
                          "Accept-Language":"ru-RU,ru;q=0.9"})
        s.mount("https://",HTTPAdapter(max_retries=Retry(
            total=3,backoff_factor=1.0,status_forcelist=[429,500,502,503,504],
            allowed_methods=["GET"],respect_retry_after_header=True)))
        THREAD.http=s
    return THREAD.http

def get(url: str, allow_missing: bool=False) -> str | None:
    safe=normalize(url)
    if not safe:
        raise ValueError(f"External source forbidden: {url}")
    r=http().get(safe,timeout=TIMEOUT)
    if allow_missing and r.status_code in (404,410):
        return None
    r.raise_for_status()
    if len(r.content)<800:
        raise RuntimeError(f"Unusually short response: {url}")
    r.encoding=r.apparent_encoding or "utf-8"
    return r.text

def sitemap_urls() -> list[str]:
    content=get(SITEMAP)
    assert content is not None
    xml=ET.fromstring(content)
    urls=[]
    for el in xml.iter():
        if not el.tag.endswith("loc") or not el.text:
            continue
        url=normalize(el.text)
        if not url or url==ROOT:
            continue
        path=urlsplit(url).path.lower()
        if not path or path.split("/")[-1] in _SKIPS:
            continue
        if any(path.startswith("/"+p+"/") for p in _SKIPS):
            continue
        urls.append(url)
    dedup=list(dict.fromkeys(urls))
    if len(dedup)<20000:
        raise RuntimeError(f"Suspiciously few sitemap URLs: {len(dedup)}")
    print(f"CHAMPION_SITEMAP total={len(dedup)}",flush=True)
    return dedup

def image_urls(soup: BeautifulSoup) -> list[str]:
    found=[]
    for a in soup.select(".p-product__image-section a.fanbox-thumber[href]"):
        raw=normalize(a.get("href",""))
        path=urlsplit(raw).path
        if not PHOTO.fullmatch(path):
            continue
        # Keep only true full-size source paths, rather than shop-generated
        # popup/thumb cache transforms. Original works on public server.
        if "/media/cache/product_popup/" in raw:
            raw=raw.replace("/media/cache/product_popup/","/media/")
        if "/media/cache/product_thumb/" in raw:
            raw=raw.replace("/media/cache/product_thumb/","/media/")
        if raw not in found:
            found.append(raw)
    return found[:40]

def product_specs(soup: BeautifulSoup) -> dict[str,str]:
    params={}
    for row in soup.select("[data-attributes-target] .p-product__attributes-group-value"):
        title=row.select_one(".p-product__attributes-group-value__title")
        body=row.select_one(".p-product__attributes-group-value-data")
        if not title or not body:
            continue
        k=clean(title.get_text(" ",strip=True)).rstrip(":")
        v=clean(body.get_text(" ",strip=True))
        if not k or not v or len(k)>100 or len(v)>700:
            continue
        if SKIP_SPEC.search(k) or BANNED.search(k+" "+v):
            continue
        params.setdefault(k,v)
    return params

def stock_status(soup: BeautifulSoup) -> tuple[str,str]:
    """true = purchase possible, false = explicitly preorder/unavailable,
    unknown = can't prove status (not silently imported as available)."""
    wrapper=soup.select_one(".p-product__form-wrap")
    if not wrapper:
        return "unknown","Не подтверждено"
    out=wrapper.select_one(".out-of-stock")
    if out:
        status=clean(out.get_text(" ",strip=True))
        if re.search(r"(?i)под заказ|предзаказ",status):
            return "false","Под заказ"
        if re.search(r"(?i)снят\s+с\s+производ",status):
            return "false","Снято с производства"
        return "false","Нет в наличии"
    form=wrapper.select_one("form#buy-form")
    if form:
        schema=wrapper.select_one("meta[itemprop='availability']")
        label=clean(schema.get("content","")).lower() if schema else ""
        if "outofstock" in label or "discontinued" in label:
            return "false","Нет в наличии"
        if label.rsplit("/",1)[-1]=="instock" and form.select_one("input[name='product_id']"):
            return "true","В наличии"
    return "unknown","Не подтверждено"

def categories(soup: BeautifulSoup) -> list[str]:
    crumbs=[]
    for entry in soup.select(".l-breadcrumb [itemprop='itemListElement']"):
        name=entry.select_one("[itemprop='name']")
        if not name:
            continue
        text=clean(name.get_text(" ",strip=True))
        if text and text.lower() not in ("главная","каталог"):
            crumbs.append(text)
    return crumbs[:-1] if len(crumbs)>1 else (crumbs if crumbs else ["Товары"])

def parse(url: str) -> tuple[str,dict|None]:
    page=get(url,allow_missing=True)
    if page is None:
        return "missing",None
    soup=BeautifulSoup(page,"lxml")
    product=soup.select_one('[itemscope][itemtype$="/Product"].p-product') or soup.select_one(".p-product[itemtype]")
    h1=soup.select_one(".p-product h1")
    sku=soup.select_one(".p-product [itemprop='sku']")
    if not product or not h1 or not sku:
        return "not_product",None
    name=clean(h1.get_text(" ",strip=True))
    article=clean(sku.get_text(" ",strip=True))
    if not name or not article or len(article)>100:
        return "invalid_article",None
    if BANNED.search(name+" "+article):
        return "dirty_title",None
    images=image_urls(soup)
    if not images:
        return "no_images",None
    state,label=stock_status(soup)
    props=product_specs(soup)
    # No raw vendor's promotional copy. Only factual compatibility sentences.
    descr=soup.select_one(".p-product__description .description-target")
    copy=""
    if descr:
        full=clean(descr.get_text(" ",strip=True))
        parts=re.split(r"(?<=[.!?])\s+",full)
        safe=[]
        for p in parts:
            if re.match(r"(?i)^(подходит для|совместим[а-я]* с|назначение|комплектация)",p) and not BANNED.search(p):
                safe.append(p)
        copy=" ".join(safe[:3])[:1200]
    cat=categories(soup)
    stable="champion-"+hashlib.sha1(urlsplit(url).path.encode()).hexdigest()[:18]
    description=clean(f"{name}. "+ " ".join(f"{k}: {v}." for k,v in props.items())+" "+copy)[:8000]
    return "product",{
        "id":stable,"article":article,"name":name,"photos":images,
        "props":props,"categories":cat,"description":description,
        "available":state,"status":label,
    }

def gather(shard: int, count: int, limit: int=0, destination: Path | None = None) -> dict:
    urls=[u for i,u in enumerate(sitemap_urls()) if i % count == shard]
    if limit:
        urls=urls[:limit]
    result=[]
    stats=Counter()
    errors=[]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures={pool.submit(parse,u):u for u in urls}
        for i,f in enumerate(as_completed(futures),1):
            try:
                kind,item=f.result()
                stats[kind]+=1
                if item is not None:
                    result.append(item)
            except Exception as exc:
                stats["error"]+=1
                if len(errors)<50:
                    errors.append(f"{futures[f]}: {exc}")
            if i%250==0:
                print(f"CHAMPION_SHARD {shard}/{count} completed={i}/{len(urls)} products={stats['product']} errors={stats['error']}",flush=True)
    if stats["error"]>max(8,len(urls)*0.02):
        raise RuntimeError(f"Too many page errors {stats['error']}/{len(urls)}: {errors[:10]}")
    if stats["product"] < len(urls)*0.80:
        raise RuntimeError(f"Unsafe shard low product ratio {stats['product']}/{len(urls)}; other={dict(stats)}")
    unknown=sum(p["available"]=="unknown" for p in result)
    if unknown>len(result)*0.08:
        raise RuntimeError(f"Too many uncertain availability statuses {unknown}/{len(result)}")
    obj={"shard":shard,"count":count,"urls":len(urls),"stats":dict(stats),"errors":errors,"items":result}
    if destination:
        destination.parent.mkdir(parents=True,exist_ok=True)
        with gzip.open(destination,"wt",encoding="utf-8",compresslevel=5) as fp:
            json.dump(obj,fp,ensure_ascii=False,separators=(",",":"))
        print(f"CHAMPION_SHARD_COMPLETE shard={shard} products={len(result)} unknown={unknown} path={destination}",flush=True)
    return obj

def create_xml(items: list[dict]) -> bytes:
    root=ET.Element("yml_catalog",date=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    shop=ET.SubElement(root,"shop")
    ET.SubElement(shop,"name").text="Мегаполис"
    ET.SubElement(shop,"company").text="Мегаполис"
    ET.SubElement(shop,"url").text="https://profikompany.ru/"
    cats=ET.SubElement(shop,"categories")
    code={}
    for item in items:
        chain=[]
        parent=None
        for name in item["categories"]:
            name=clean(name)
            chain.append(name)
            path=tuple(chain)
            if path not in code:
                ident=str(len(code)+1)
                tag=ET.SubElement(cats,"category",id=ident)
                if parent:
                    tag.set("parentId",parent)
                tag.text=name
                code[path]=ident
            parent=code[path]
        item["_category"]=parent or ""
    offers=ET.SubElement(shop,"offers")
    for p in sorted(items,key=lambda a:a["id"]):
        attrs={"id":p["id"]}
        if p["available"] in ("true","false"):
            attrs["available"]=p["available"]
        o=ET.SubElement(offers,"offer",attrs)
        ET.SubElement(o,"name").text=p["name"]
        if p["_category"]:
            ET.SubElement(o,"categoryId").text=p["_category"]
        ET.SubElement(o,"vendorCode").text=p["article"]
        ET.SubElement(o,"model").text=p["article"]
        for im in p["photos"]:
            ET.SubElement(o,"picture").text=im
        if p["description"]:
            ET.SubElement(o,"description").text=p["description"]
        for k,v in sorted(p["props"].items()):
            ET.SubElement(o,"param",name=k).text=v
        ET.SubElement(o,"param",name="Наличие").text=p["status"]
        # Price and numeric stock are intentionally absent.
    return ET.tostring(root,encoding="utf-8",xml_declaration=True)

def merge(directory: Path, out: Path, expected_shards: int) -> None:
    parts=sorted(directory.glob("shard-*.json.gz"))
    if len(parts)!=expected_shards:
        raise RuntimeError(f"Expected {expected_shards} shards; got {len(parts)}")
    indices=set()
    items=[]
    total_urls=0
    for path in parts:
        with gzip.open(path,"rt",encoding="utf-8") as fp:
            part=json.load(fp)
        if part["count"]!=expected_shards or part["shard"] in indices:
            raise RuntimeError(f"Duplicate/bad shard {path.name}")
        indices.add(part["shard"])
        total_urls+=part["urls"]
        items.extend(part["items"])
    if indices!=set(range(expected_shards)):
        raise RuntimeError("Not all shards are present")
    if len(items)<MIN_PRODUCTS or len(items)<total_urls*0.80:
        raise RuntimeError(f"Incomplete results: products={len(items)} URLs={total_urls}")
    ids=[i["id"] for i in items]
    if len(set(ids))!=len(ids):
        raise RuntimeError("Duplicate stable IDs")
    articles=[i["article"].casefold() for i in items]
    duplicate_count=len(articles)-len(set(articles))
    if duplicate_count:
        # Duplicate original manufacturer codes can exist for replacements.
        # IDs remain stable, and such codes are reported for YML Pro mapping.
        print(f"CHAMPION_DUPLICATE_ARTICLE_CODES count={duplicate_count}",flush=True)
    previous=0
    if out.exists():
        previous=len(ET.parse(out).getroot().findall("./shop/offers/offer"))
        if previous>=MIN_PRODUCTS and len(items)<previous*(1-MAX_SHRINK):
            raise RuntimeError(f"Unsafe feed shrink {previous} -> {len(items)}")
    xml=create_xml(items)
    ET.fromstring(xml)
    out.parent.mkdir(parents=True,exist_ok=True)
    tmp=out.with_suffix(".tmp")
    tmp.write_bytes(xml)
    tmp.replace(out)
    states=Counter(p["available"] for p in items)
    print(f"CHAMPION_PUBLISHED offers={len(items)} images={sum(len(x['photos']) for x in items)} article_duplicates={duplicate_count} states={dict(states)} output={out} bytes={len(xml)}",flush=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("mode",choices=["shard","merge"])
    parser.add_argument("--shard",type=int,default=0)
    parser.add_argument("--shards",type=int,default=12)
    parser.add_argument("--limit",type=int,default=0)
    parser.add_argument("--destination",type=Path)
    parser.add_argument("--directory",type=Path,default=Path("championtool/parts"))
    parser.add_argument("--out",type=Path,default=Path("championtool/catalog-content.yml"))
    args=parser.parse_args()
    if args.mode=="shard":
        gather(args.shard,args.shards,args.limit,args.destination or Path(f"championtool/parts/shard-{args.shard}.json.gz"))
    else:
        merge(args.directory,args.out,args.shards)

if __name__=="__main__":
    main()
