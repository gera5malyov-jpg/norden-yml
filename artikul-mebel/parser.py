from __future__ import annotations

import concurrent.futures
import hashlib
import os
import re
import sys
import time
import zlib
from collections import deque
from datetime import datetime
from typing import Iterable
from urllib.parse import urljoin, urlparse, urlunparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

BASE_URL = 'https://artikul-mebel.ru'
CATALOG_URL = f'{BASE_URL}/catalog/'
SITEMAP_URL = f'{BASE_URL}/sitemap.xml'
OUTPUT = os.getenv('OUTPUT', 'catalog.yml')
MIN_PRODUCTS = int(os.getenv('MIN_PRODUCTS', '100'))
WORKERS = max(1, int(os.getenv('WORKERS', '4')))
REQUEST_TIMEOUT = int(os.getenv('REQUEST_TIMEOUT', '45'))
USER_AGENT = os.getenv(
    'USER_AGENT',
    'Mozilla/5.0 (compatible; MegapolisCatalogBot/1.0; +https://github.com/gera5malyov-jpg)'
)

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': USER_AGENT,
    'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.5',
})


def clean_text(value: str | None) -> str:
    if not value:
        return ''
    return re.sub(r'\s+', ' ', value.replace('\xa0', ' ')).strip()


def money_to_float(value: str | None) -> float | None:
    if not value:
        return None
    s = value.replace('\xa0', ' ').replace(' ', '').replace(',', '.')
    s = re.sub(r'[^0-9.]', '', s)
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def format_money(value: float | None) -> str:
    if value is None:
        return '0'
    if float(value).is_integer():
        return str(int(value))
    return f'{value:.2f}'.rstrip('0').rstrip('.')


def canonical_url(url: str) -> str:
    p = urlparse(url)
    # Keep query because Bitrix pagination can live there; drop fragments.
    return urlunparse((p.scheme or 'https', p.netloc, p.path, '', p.query, ''))


def is_same_site(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host in {'artikul-mebel.ru', 'www.artikul-mebel.ru'}


def fetch(url: str, *, binary: bool = False):
    last_error = None
    for attempt in range(1, 4):
        try:
            r = SESSION.get(url, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.content if binary else r.text
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(attempt * 1.5)
    raise RuntimeError(f'Не удалось загрузить {url}: {last_error}')


def parse_sitemap_urls(xml_bytes: bytes) -> tuple[list[str], list[str]]:
    root = ET.fromstring(xml_bytes)
    tag = root.tag.split('}')[-1]
    locs = [clean_text(e.text) for e in root.iter() if e.tag.split('}')[-1] == 'loc' and clean_text(e.text)]
    if tag == 'sitemapindex':
        return [], locs
    product_urls = []
    for url in locs:
        if '/catalog/detail/' in url:
            product_urls.append(canonical_url(url))
    return list(dict.fromkeys(product_urls)), []


def discover_from_sitemap() -> list[str]:
    queue = deque([SITEMAP_URL])
    seen_maps = set()
    products: list[str] = []
    while queue and len(seen_maps) < 50:
        sitemap = queue.popleft()
        if sitemap in seen_maps:
            continue
        seen_maps.add(sitemap)
        try:
            data = fetch(sitemap, binary=True)
            urls, nested = parse_sitemap_urls(data)
            products.extend(urls)
            for url in nested:
                if is_same_site(url):
                    queue.append(url)
        except Exception as exc:
            print(f'[sitemap] {exc}', file=sys.stderr)
    return list(dict.fromkeys(products))


def discover_by_crawl(max_pages: int = 1200) -> list[str]:
    queue = deque([CATALOG_URL])
    seen = set()
    products: list[str] = []
    while queue and len(seen) < max_pages:
        url = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        try:
            html = fetch(url)
        except Exception as exc:
            print(f'[crawl] skip {url}: {exc}', file=sys.stderr)
            continue
        soup = BeautifulSoup(html, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = canonical_url(urljoin(url, a['href']))
            if not is_same_site(href):
                continue
            parsed = urlparse(href)
            if '/catalog/detail/' in parsed.path:
                products.append(href)
                continue
            if parsed.path.startswith('/catalog/'):
                # Ignore filters/sorts, but preserve Bitrix PAGEN_* pagination.
                if parsed.query and 'PAGEN_' not in parsed.query.upper():
                    continue
                if href not in seen:
                    queue.append(href)
        if len(seen) % 25 == 0:
            print(f'[crawl] pages={len(seen)} products={len(set(products))}')
    return list(dict.fromkeys(products))


def discover_product_urls() -> list[str]:
    products = discover_from_sitemap()
    if len(products) >= MIN_PRODUCTS:
        print(f'Найдено через sitemap: {len(products)} товаров')
        return products
    print(f'Sitemap дал только {len(products)} товаров; запускаю обход каталога')
    crawled = discover_by_crawl()
    combined = list(dict.fromkeys(products + crawled))
    print(f'Найдено после обхода каталога: {len(combined)} товаров')
    return combined


def find_detail_container(soup: BeautifulSoup):
    selectors = [
        '.product-detail', '.catalog-detail', '.detail-product', '.product_detail',
        '[class*="catalog-detail"]', '[class*="product-detail"]', 'main'
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            return node
    return soup.body or soup


def detect_category(soup: BeautifulSoup, fallback: str | None = None) -> str:
    candidates = []
    for a in soup.select('[class*="breadcrumb"] a[href]'):
        href = urljoin(BASE_URL, a.get('href', ''))
        text = clean_text(a.get_text(' ', strip=True))
        if '/catalog/' in href and '/detail/' not in href and text and text.lower() != 'каталог':
            candidates.append(text)
    if candidates:
        return candidates[-1]
    return clean_text(fallback) or 'Каталог'


def extract_pictures(soup: BeautifulSoup, container) -> list[str]:
    candidates: list[str] = []
    og = soup.find('meta', attrs={'property': 'og:image'})
    if og and og.get('content'):
        candidates.append(og['content'])

    for img in container.find_all('img'):
        for attr in ('data-src', 'data-lazy', 'src'):
            if img.get(attr):
                candidates.append(img[attr])
                break
    for a in container.find_all('a', href=True):
        candidates.append(a['href'])

    result = []
    for raw in candidates:
        url = urljoin(BASE_URL, raw)
        low = url.lower().split('?', 1)[0]
        if not re.search(r'\.(?:jpe?g|png|webp)$', low):
            continue
        if not is_same_site(url):
            continue
        if any(bad in low for bad in ('logo', 'icon', 'sprite', 'favicon', 'loader')):
            continue
        if '/upload/' not in low and '/images/' not in low:
            continue
        if url not in result:
            result.append(url)
        if len(result) >= 10:
            break
    return result


def extract_params(container) -> dict[str, str]:
    params: dict[str, str] = {}
    for tr in container.find_all('tr'):
        cells = [clean_text(c.get_text(' ', strip=True)) for c in tr.find_all(['th', 'td'])]
        cells = [c for c in cells if c]
        if len(cells) >= 2:
            key, value = cells[0], cells[-1]
            if key != value and len(key) <= 160 and value:
                params[key] = value
    for dl in container.find_all('dl'):
        dts = dl.find_all('dt')
        dds = dl.find_all('dd')
        for dt, dd in zip(dts, dds):
            key = clean_text(dt.get_text(' ', strip=True))
            value = clean_text(dd.get_text(' ', strip=True))
            if key and value:
                params.setdefault(key, value)
    return params


def extract_description(soup: BeautifulSoup, container) -> str:
    paragraphs = []
    for p in container.find_all('p'):
        text = clean_text(p.get_text(' ', strip=True))
        if len(text) >= 30 and text not in paragraphs:
            paragraphs.append(text)
    if paragraphs:
        return ' '.join(paragraphs[:8])[:5000]
    meta = soup.find('meta', attrs={'name': 'description'})
    if meta and meta.get('content'):
        return clean_text(meta['content'])[:5000]
    return ''


def parse_product(html: str, url: str, category_hint: str | None = None) -> dict:
    soup = BeautifulSoup(html, 'html.parser')
    h1 = soup.find('h1')
    name = clean_text(h1.get_text(' ', strip=True) if h1 else '')
    if not name:
        raise ValueError(f'Нет H1 у товара {url}')

    container = find_detail_container(soup)
    text = clean_text(container.get_text(' ', strip=True))

    sku_match = re.search(r'Арт\.?\s*:?\s*([A-Za-zА-Яа-яЁё0-9._/\-]+)', text, flags=re.I)
    sku = sku_match.group(1).strip() if sku_match else ''

    retail_match = re.search(r'Розничная\s+стоимость\s*([0-9\s\xa0]+(?:[.,][0-9]+)?)\s*₽', text, flags=re.I)
    price = money_to_float(retail_match.group(1)) if retail_match else None
    if price is None:
        any_price = re.search(r'([0-9][0-9\s\xa0]*(?:[.,][0-9]+)?)\s*₽', text)
        price = money_to_float(any_price.group(1)) if any_price else 0.0

    bulk_match = re.search(
        r'([0-9\s\xa0]+(?:[.,][0-9]+)?)\s*₽\s*при\s+заказе\s+от\s*([0-9\s\xa0]+)\s*шт',
        text, flags=re.I
    )
    bulk_price = money_to_float(bulk_match.group(1)) if bulk_match else None
    bulk_min_qty = int(re.sub(r'\D', '', bulk_match.group(2))) if bulk_match else None

    stock_match = re.search(r'В\s+наличии\s*:?\s*(\d+)', text, flags=re.I)
    stock = int(stock_match.group(1)) if stock_match else None

    return {
        'url': url,
        'name': name,
        'sku': sku,
        'category': detect_category(soup, category_hint),
        'price': float(price or 0),
        'bulk_price': bulk_price,
        'bulk_min_qty': bulk_min_qty,
        'stock': stock,
        'description': extract_description(soup, container),
        'pictures': extract_pictures(soup, container),
        'params': extract_params(container),
    }


def category_id(name: str) -> str:
    return str((zlib.crc32(name.encode('utf-8')) & 0x7FFFFFFF) + 1)


def offer_id(product: dict, used: set[str]) -> str:
    raw = clean_text(product.get('sku'))
    if raw:
        candidate = re.sub(r'[^0-9A-Za-zА-Яа-яЁё._\-]+', '_', raw)[:80]
    else:
        candidate = hashlib.sha1(product['url'].encode('utf-8')).hexdigest()[:20]
    if candidate not in used:
        used.add(candidate)
        return candidate
    candidate = f"{candidate[:65]}-{hashlib.sha1(product['url'].encode('utf-8')).hexdigest()[:10]}"
    used.add(candidate)
    return candidate


def build_yml(products: Iterable[dict]) -> bytes:
    products = list(products)
    root = ET.Element('yml_catalog', {'date': datetime.now().strftime('%Y-%m-%d %H:%M')})
    shop = ET.SubElement(root, 'shop')
    ET.SubElement(shop, 'name').text = 'Артикул-Мебель'
    ET.SubElement(shop, 'company').text = 'ООО «Артикул-Мебель»'
    ET.SubElement(shop, 'url').text = BASE_URL + '/'

    currencies = ET.SubElement(shop, 'currencies')
    ET.SubElement(currencies, 'currency', {'id': 'RUR', 'rate': '1'})

    category_names = sorted({clean_text(p.get('category')) or 'Каталог' for p in products})
    categories = ET.SubElement(shop, 'categories')
    cat_ids = {}
    for name in category_names:
        cid = category_id(name)
        cat_ids[name] = cid
        ET.SubElement(categories, 'category', {'id': cid}).text = name

    offers = ET.SubElement(shop, 'offers')
    used_ids: set[str] = set()
    for product in sorted(products, key=lambda p: (p.get('category', ''), p.get('name', ''), p.get('url', ''))):
        price = float(product.get('price') or 0)
        offer = ET.SubElement(offers, 'offer', {
            'id': offer_id(product, used_ids),
            'available': 'true' if price > 0 else 'false',
        })
        ET.SubElement(offer, 'url').text = product['url']
        ET.SubElement(offer, 'price').text = format_money(price)
        ET.SubElement(offer, 'currencyId').text = 'RUR'
        category = clean_text(product.get('category')) or 'Каталог'
        ET.SubElement(offer, 'categoryId').text = cat_ids[category]
        for picture in product.get('pictures', [])[:10]:
            ET.SubElement(offer, 'picture').text = picture
        ET.SubElement(offer, 'name').text = product['name']
        ET.SubElement(offer, 'vendor').text = 'Артикул-Мебель'
        if product.get('sku'):
            ET.SubElement(offer, 'vendorCode').text = product['sku']
        if product.get('description'):
            ET.SubElement(offer, 'description').text = product['description']

        for key, value in product.get('params', {}).items():
            if clean_text(key) and clean_text(value):
                ET.SubElement(offer, 'param', {'name': clean_text(key)}).text = clean_text(value)
        if product.get('bulk_price') is not None:
            ET.SubElement(offer, 'param', {'name': 'Оптовая цена'}).text = format_money(product['bulk_price'])
        if product.get('bulk_min_qty') is not None:
            ET.SubElement(offer, 'param', {'name': 'Минимальное количество для оптовой цены'}).text = str(product['bulk_min_qty'])
        if product.get('stock') is not None:
            ET.SubElement(offer, 'param', {'name': 'Остаток'}).text = str(product['stock'])
        if price <= 0:
            ET.SubElement(offer, 'param', {'name': 'Цена по запросу'}).text = 'Да'

    ET.indent(root, space='  ')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def load_one(url: str) -> dict | None:
    try:
        return parse_product(fetch(url), url)
    except Exception as exc:
        print(f'[product] skip {url}: {exc}', file=sys.stderr)
        return None


def main() -> None:
    urls = discover_product_urls()
    if len(urls) < MIN_PRODUCTS:
        raise RuntimeError(
            f'Найдено только {len(urls)} ссылок на товары; минимум для безопасного обновления {MIN_PRODUCTS}. '
            'Предыдущий YML не будет перезаписан.'
        )

    products: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(load_one, url): url for url in urls}
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            product = future.result()
            if product:
                products.append(product)
            if i % 25 == 0 or i == len(futures):
                print(f'Обработано {i}/{len(futures)}, успешно {len(products)}')

    if len(products) < MIN_PRODUCTS:
        raise RuntimeError(
            f'Успешно разобрано только {len(products)} товаров; минимум {MIN_PRODUCTS}. '
            'Предыдущий YML не будет перезаписан.'
        )

    data = build_yml(products)
    if len(data) < 10_000:
        raise RuntimeError(f'Сформированный YML подозрительно мал: {len(data)} байт')

    tmp = OUTPUT + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, OUTPUT)
    print(f'Готово: {OUTPUT}; товаров={len(products)}; размер={len(data)} байт')


if __name__ == '__main__':
    main()
