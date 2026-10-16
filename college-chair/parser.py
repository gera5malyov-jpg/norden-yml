#!/usr/bin/env python3
"""College-chair public catalog -> content-only YML for Webasyst YML Pro.

Price and stock deliberately NOT exported: they come later from supplier Excel.
Do not enable price/stock imports against this content-only file.
"""
from __future__ import annotations

import os
import re
import tempfile
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://college-chair.ru"
CATEGORIES = {
    "7": "Кресла для руководителей",
    "8": "Кресла для персонала",
    "16": "Кресла для посетителей",
    "17": "Геймерские кресла",
    "18": "Детские кресла",
}
OUTPUT = Path(os.getenv("COLLEGE_OUTPUT", "college-chair/catalog-content.yml"))
WORKERS = max(1, min(6, int(os.getenv("COLLEGE_WORKERS", "4"))))
MIN_OFFERS = int(os.getenv("COLLEGE_MIN_OFFERS", "120"))
MAX_LISTING_PAGES = 100
THREAD_LOCAL = threading.local()
PRODUCT_PATH = re.compile(r"^/catalog/(\d+)/(\d+)/?$")
IMAGE_PATH = re.compile(r"^/upload/(?:iblock|uf)/.+\.(?:jpe?g|png|webp)$", re.I)
BAD_KEYS = re.compile(
    r"(?i)бренд|производител|поставщик|дистрибьютор|"
    r"артикул|рекомендуем|комментар|проголосовав|"
    r"сумма оценок|рейтинг|новинка|^id\b|продаж|контакт|сайт"
)
BAD_VALUES = re.compile(
    r"(?i)college-chair\.ru|realchair\.ru|"
    r"https?://|www\.|@\w+\.\w+|"
    r"телефон|дистрибьютор|поставщик|дилер|"
    r"\+7[\s(]*\d{3}|8[\s(]*800"
)

def clean(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("\xa0", " ")).strip()

def session() -> requests.Session:
    if not hasattr(THREAD_LOCAL, "http"):
        sess = requests.Session()
        sess.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; Megapolis-Catalog/1.0)",
            "Accept-Language": "ru-RU,ru;q=0.9",
        })
        retry = Retry(
            total=3, connect=3, read=3, status=3, backoff_factor=1.0,
            allowed_methods=["GET"], status_forcelist=[429, 500, 502, 503, 504],
            respect_retry_after_header=True,
        )
        sess.mount("https://", HTTPAdapter(max_retries=retry))
        THREAD_LOCAL.http = sess
    return THREAD_LOCAL.http

def canonical(url: str) -> str:
    value = urlsplit(urljoin(BASE, url or ""))
    if value.scheme != "https" or value.hostname not in ("college-chair.ru", "www.college-chair.ru"):
        return ""
    # No external or embedded tracking destinations are ever exported.
    return urlunsplit(("https", "college-chair.ru", value.path, value.query, ""))

def get(url: str) -> str:
    safe = canonical(url)
    if not safe:
        raise ValueError("Offsite URL rejected")
    response = session().get(safe, timeout=(15, 40))
    response.raise_for_status()
    if len(response.content) < 1100:
        raise RuntimeError(f"Unexpected short response: {safe}")
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text

def get_product_path(link: str, category: str | None = None) -> tuple[str, str] | None:
    parsed = urlsplit(canonical(link))
    matched = PRODUCT_PATH.fullmatch(parsed.path)
    if not matched:
        return None
    cat, pid = matched.groups()
    if cat not in CATEGORIES or (category and cat != category):
        return None
    return cat, pid

def listing_links(html: str, cat: str, url: str) -> tuple[set[str], set[str]]:
    soup = BeautifulSoup(html, "html.parser")
    products, pages = set(), set()
    for a in soup.select("a[href]"):
        link = canonical(a.get("href", ""))
        match = get_product_path(link, cat)
        if match:
            products.add(f"{BASE}/catalog/{cat}/{match[1]}")
            continue
        parts = urlsplit(link)
        if parts.path.rstrip("/") != f"/catalog/{cat}":
            continue
        params = parse_qs(parts.query)
        if any(k.lower().startswith(("pagen_", "page", "nav")) for k in params):
            pages.add(link)
    return products, pages

def discover() -> dict[str, str]:
    todo = deque((cat, f"{BASE}/catalog/{cat}/") for cat in CATEGORIES)
    seen, products, counts = set(), {}, {c: 0 for c in CATEGORIES}
    while todo:
        cat, url = todo.popleft()
        if url in seen:
            continue
        seen.add(url)
        if len(seen) > MAX_LISTING_PAGES:
            raise RuntimeError("Excessive listing pagination")
        urls, pages = listing_links(get(url), cat, url)
        if not urls:
            raise RuntimeError(f"No products found on a catalog page: {url}")
        for product in urls:
            prev = products.setdefault(product, cat)
            if prev != cat:
                raise RuntimeError(f"Conflicting category: {product}")
        counts[cat] += len(urls)
        todo.extend((cat, p) for p in sorted(pages) if p not in seen)
        print(f"LIST category={cat} page={url} products={len(urls)}", flush=True)
    if any(v == 0 for v in counts.values()):
        raise RuntimeError(f"One or more categories empty: {counts}")
    if len(products) < MIN_OFFERS:
        raise RuntimeError(f"Only {len(products)} product URLs; minimum {MIN_OFFERS}")
    print(f"DISCOVERED unique_product_urls={len(products)} counts={counts}", flush=True)
    return products

def properties(soup: BeautifulSoup) -> dict[str, str]:
    result = {}
    for row in soup.select("#tb2 table.propsTable tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 2:
            continue
        key = clean(cells[0].get_text(" ", strip=True)).rstrip(":")
        value = clean(cells[1].get_text(" ", strip=True))
        if not key or not value or len(key) > 90 or len(value) > 350:
            continue
        if BAD_KEYS.search(key) or BAD_VALUES.search(value):
            continue
        result.setdefault(key, value)
    return result

def pictures(html: str, soup: BeautifulSoup) -> list[str]:
    candidates = [a.get("href") for a in soup.select(".product a.imagelightbox[href]")]
    # Bitrix slider is the authoritative source of all images for this product.
    scripts = soup.find_all("script", string=lambda x: bool(x and "new JCCatalogElement(" in x))
    for script in scripts:
        match = re.search(r"'SLIDER':\[(.*?)\]\s*,\s*'CAN_BUY'", script.string or "", re.S)
        if match:
            candidates.extend(re.findall(r"'SRC':'((?:\\.|[^'\\])*)'", match.group(1)))
    result = []
    for item in candidates:
        link = canonical(str(item or "").replace("\\/", "/"))
        if IMAGE_PATH.fullmatch(urlsplit(link).path) and link not in result:
            result.append(link)
    return result[:40]

def description(cat: str, sku: str, specs: dict[str, str]) -> str:
    # Build factual copy from the table, never scrape promotional text.
    bits = [f"{CATEGORIES[cat].rstrip('а')} - модель {sku}."]
    for field in (
        "Цвет", "Обивка", "Механизм качания", "Подлокотники",
        "Крестовина", "Ролики", "Максимальная нагрузка",
        "Размеры кресла", "Высота кресла", "Высота спинки",
    ):
        if field in specs:
            bits.append(f"{field}: {specs[field]}.")
    return " ".join(bits)

def parse_product(url: str, cat: str) -> dict:
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")
    block = soup.select_one(".bx_item_detail .product")
    if not block:
        raise ValueError(f"Not a product detail page: {url}")
    match = get_product_path(url, cat)
    if not match:
        raise ValueError(f"Wrong category: {url}")
    pid = match[1]
    source = clean(block.get_text(" ", strip=True))
    art = re.search(r"Артикул\s*:\s*(.+?)(?=\s*(?:\(|\)|Бренд|осталось|$))", source)
    # Preferred: first direct article element, before reviews or specs.
    initial = block.select_one("li p a[href='#']")
    sku = clean(re.sub(r"^Артикул\s*:\s*", "", initial.get_text(" ", strip=True), flags=re.I)) if initial else ""
    if not sku:
        sku = clean(art.group(1)) if art else ""
    if not sku or len(sku) > 100:
        raise ValueError(f"Missing/invalid vendor article: {url}")
    specs = properties(soup)
    images = pictures(html, soup)
    if not images:
        raise ValueError(f"Missing source images: {url}")
    category_label = {
        "7": "Кресло для руководителя",
        "8": "Кресло для персонала",
        "16": "Кресло для посетителей",
        "17": "Кресло игровое",
        "18": "Кресло детское",
    }[cat]
    return {
        "id": f"college-{pid}",
        "sku": sku,
        "name": f"{category_label} {sku}",
        "category": cat,
        "images": images,
        "params": specs,
        "description": description(cat, sku, specs),
    }

def build_xml(items: list[dict]) -> bytes:
    root = ET.Element("yml_catalog", date=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    shop = ET.SubElement(root, "shop")
    ET.SubElement(shop, "name").text = "Мегаполис"
    ET.SubElement(shop, "company").text = "Мегаполис"
    ET.SubElement(shop, "url").text = "https://profikompany.ru/"
    cats = ET.SubElement(shop, "categories")
    for key, value in CATEGORIES.items():
        ET.SubElement(cats, "category", id=key).text = value
    offers = ET.SubElement(shop, "offers")
    for item in sorted(items, key=lambda o: (int(o["category"]), o["sku"])):
        offer = ET.SubElement(offers, "offer", id=item["id"])
        ET.SubElement(offer, "name").text = item["name"]
        ET.SubElement(offer, "categoryId").text = item["category"]
        ET.SubElement(offer, "vendorCode").text = item["sku"]
        ET.SubElement(offer, "model").text = item["sku"]
        # price, quantity, available intentionally absent. No fallback zero/one.
        for picture in item["images"]:
            ET.SubElement(offer, "picture").text = picture
        ET.SubElement(offer, "description").text = item["description"]
        for k, v in sorted(item["params"].items()):
            ET.SubElement(offer, "param", name=k).text = v
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)

def validate(xml_bytes: bytes, previous: Path | None = None) -> tuple[int, int]:
    root = ET.fromstring(xml_bytes)
    offers = root.findall("./shop/offers/offer")
    skus, ids = set(), set()
    if len(offers) < MIN_OFFERS:
        raise RuntimeError(f"Feed below minimum: {len(offers)}")
    counts = {k: 0 for k in CATEGORIES}
    picture_count = 0
    for item in offers:
        sku = item.findtext("vendorCode")
        ident = item.get("id")
        cat = item.findtext("categoryId")
        if not sku or not ident or sku.casefold() in skus or ident in ids:
            raise RuntimeError(f"Missing/duplicate article or ID: {sku!r} {ident!r}")
        if cat not in counts:
            raise RuntimeError(f"Unknown category for {ident}")
        skus.add(sku.casefold())
        ids.add(ident)
        counts[cat] += 1
        pics = item.findall("picture")
        picture_count += len(pics)
        if not pics:
            raise RuntimeError(f"No photo: {ident}")
        if item.find("price") is not None or item.find("available") is not None:
            raise RuntimeError("Content-only feed must never overwrite prices or stocks")
        if "college-chair" in (item.findtext("description") or "").lower():
            raise RuntimeError(f"Vendor mention in description: {ident}")
    if any(count == 0 for count in counts.values()):
        raise RuntimeError(f"Missing category in output: {counts}")
    if previous and previous.exists():
        old_count = len(ET.parse(previous).getroot().findall("./shop/offers/offer"))
        if old_count >= MIN_OFFERS and len(offers) < old_count * 0.80:
            raise RuntimeError(f"Unsafe catalog shrink {old_count} -> {len(offers)}")
    return len(offers), picture_count

def main() -> None:
    links = discover()
    items, errors = [], []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        pending = {pool.submit(parse_product, url, cat): url for url, cat in links.items()}
        for i, future in enumerate(as_completed(pending), 1):
            try:
                items.append(future.result())
            except Exception as exc:
                errors.append(f"{pending[future]}: {exc}")
            if i % 40 == 0:
                print(f"PARSED={i}/{len(pending)} errors={len(errors)}", flush=True)
    if errors:
        raise RuntimeError(f"Incomplete parse ({len(errors)} errors), old YML preserved: {errors[:8]}")
    xml_bytes = build_xml(items)
    count, photos = validate(xml_bytes, OUTPUT)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=OUTPUT.parent, suffix=".tmp", delete=False) as temp:
        temp.write(xml_bytes)
        temp_path = Path(temp.name)
    os.replace(temp_path, OUTPUT)
    print(f"COLLEGE_SUCCESS offers={count} images={photos} saved={OUTPUT}", flush=True)

if __name__ == "__main__":
    main()
