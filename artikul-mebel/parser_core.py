from __future__ import annotations

import concurrent.futures
import hashlib
import html as html_lib
import os
import re
import sys
import time
import zlib
from collections import deque
from datetime import datetime
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup, Tag

BASE_URL = 'https://artikul-mebel.ru'
CATALOG_URL = f'{BASE_URL}/catalog/'
SITEMAP_URL = f'{BASE_URL}/sitemap.xml'
OUTPUT = os.getenv('OUTPUT', 'catalog.yml')
MIN_PRODUCTS = int(os.getenv('MIN_PRODUCTS', '100'))
WORKERS = max(1, int(os.getenv('WORKERS', '4')))
REQUEST_TIMEOUT = int(os.getenv('REQUEST_TIMEOUT', '45'))
USER_AGENT = os.getenv(
    'USER_AGENT',
    'Mozilla/5.0 (compatible; MegapolisCatalogBot/1.1; +https://github.com/gera5malyov-jpg)'
)

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': USER_AGENT,
    'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.5',
})

ARTICLE_RE = re.compile(
    r'(?:^|\s)Арт\.?\s*:\s*([A-Za-zА-Яа-яЁё0-9._/\-]+)',
    flags=re.I,
)


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
    return urlunparse((p.scheme or 'https', p.netloc, p.path, '', p.query, ''))


def is_same_site(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host in {'artikul-mebel.ru', 'www.artikul-mebel.ru'}


def fetch(url: str, *, binary: bool = False):
    last_error = None
    for attempt in range(1, 4):
        try:
            response = SESSION.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.content if binary else response.text
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(attempt * 1.5)
    raise RuntimeError(f'Не удалось загрузить {url}: {last_error}')


def parse_sitemap_urls(xml_bytes: bytes) -> tuple[list[str], list[str]]:
    root = ET.fromstring(xml_bytes)
    tag = root.tag.split('}')[-1]
    locs = [
        clean_text(element.text)
        for element in root.iter()
        if element.tag.split('}')[-1] == 'loc' and clean_text(element.text)
    ]
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
            page_html = fetch(url)
        except Exception as exc:
            print(f'[crawl] skip {url}: {exc}', file=sys.stderr)
            continue

        soup = BeautifulSoup(page_html, 'html.parser')
        for anchor in soup.find_all('a', href=True):
            href = canonical_url(urljoin(url, anchor['href']))
            if not is_same_site(href):
                continue

            parsed = urlparse(href)
            if '/catalog/detail/' in parsed.path:
                products.append(href)
                continue

            if parsed.path.startswith('/catalog/'):
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
        '.product-detail',
        '.catalog-detail',
        '.detail-product',
        '.product_detail',
        '[class*="catalog-detail"]',
        '[class*="product-detail"]',
        'main',
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            return node
    return soup.body or soup


def detect_category(soup: BeautifulSoup, fallback: str | None = None) -> str:
    candidates = []
    for anchor in soup.select('[class*="breadcrumb"] a[href]'):
        href = urljoin(BASE_URL, anchor.get('href', ''))
        text = clean_text(anchor.get_text(' ', strip=True))
        if '/catalog/' in href and '/detail/' not in href and text and text.lower() != 'каталог':
            candidates.append(text)
    if candidates:
        return candidates[-1]
    return clean_text(fallback) or 'Каталог'


def extract_sku(soup: BeautifulSoup, container) -> str:
    scopes: list[str] = []

    article_node = soup.select_one('.detail-article')
    if article_node:
        scopes.append(clean_text(article_node.get_text(' ', strip=True)))

    container_text = clean_text(container.get_text(' ', strip=True))
    if container_text:
        scopes.append(container_text)

    full_text = clean_text(soup.get_text(' ', strip=True))
    if full_text:
        scopes.append(full_text)

    for scope in scopes:
        match = ARTICLE_RE.search(scope)
        if match:
            return match.group(1).strip()
    return ''


def _append_unique(values: list[str], value: str | None) -> None:
    text = clean_text(value)
    if text and text not in values:
        values.append(text)


def _image_url(raw: str | None) -> str | None:
    if not raw:
        return None

    url = urljoin(BASE_URL, html_lib.unescape(raw))
    low = url.lower().split('?', 1)[0]
    if not re.search(r'\.(?:jpe?g|png|webp)$', low):
        return None
    if not is_same_site(url):
        return None
    if any(bad in low for bad in ('logo', 'icon', 'sprite', 'favicon', 'loader', 'ufo.webp')):
        return None
    if '/upload/' not in low and '/images/' not in low:
        return None
    return url


def extract_pictures(soup: BeautifulSoup, container) -> list[str]:
    candidates: list[str] = []

    og = soup.find('meta', attrs={'property': 'og:image'})
    if og and og.get('content'):
        candidates.append(og['content'])

    selectors = [
        '.wrapper-big-picture img',
        '.big-picture img',
        '[data-popup-gallery] img',
        '[class*="photo-gallery"] img',
        '[class*="photogallery"] img',
        '[class*="product-gallery"] img',
        '.product-detail img',
        '.catalog-detail img',
    ]

    seen_nodes: set[int] = set()
    for selector in selectors:
        for image in soup.select(selector):
            node_id = id(image)
            if node_id in seen_nodes:
                continue
            seen_nodes.add(node_id)
            for attr in ('data-big-src', 'data-src', 'data-lazy', 'src'):
                if image.get(attr):
                    candidates.append(image[attr])
                    break

    for image in container.find_all('img'):
        if id(image) in seen_nodes:
            continue
        for attr in ('data-big-src', 'data-src', 'data-lazy', 'src'):
            if image.get(attr):
                candidates.append(image[attr])
                break

    for anchor in soup.select(
        '.wrapper-big-picture a[href], .big-picture a[href], [class*="gallery"] a[href]'
    ):
        candidates.append(anchor['href'])

    result: list[str] = []
    for raw in candidates:
        url = _image_url(raw)
        if url and url not in result:
            result.append(url)
        if len(result) >= 20:
            break
    return result


def _characteristics_section(soup: BeautifulSoup, container):
    for selector in (
        '#chars',
        '#characteristics',
        '[id="characteristics"]',
        '[data-section="characteristics"]',
    ):
        node = soup.select_one(selector)
        if node:
            return node
    return container


def _add_param(params: dict[str, str], key: str | None, value: str | None) -> None:
    key_text = clean_text(key)
    value_text = clean_text(value)
    if not key_text or not value_text or key_text == value_text:
        return
    if len(key_text) > 160 or len(value_text) > 2000:
        return
    if key_text.lower() in {'технические характеристики', 'характеристики'}:
        return
    params.setdefault(key_text, value_text)


def extract_params(soup: BeautifulSoup, container) -> dict[str, str]:
    section = _characteristics_section(soup, container)
    params: dict[str, str] = {}

    for row in section.find_all('tr'):
        cells = [clean_text(cell.get_text(' ', strip=True)) for cell in row.find_all(['th', 'td'])]
        cells = [cell for cell in cells if cell]
        if len(cells) >= 2:
            _add_param(params, cells[0], cells[-1])

    for description_list in section.find_all('dl'):
        terms = description_list.find_all('dt')
        definitions = description_list.find_all('dd')
        for term, definition in zip(terms, definitions):
            _add_param(
                params,
                term.get_text(' ', strip=True),
                definition.get_text(' ', strip=True),
            )

    row_selectors = (
        '.char-row',
        '[class*="char-row"]',
        '[class*="characteristic-row"]',
        '[class*="property-row"]',
        '[class*="prop-row"]',
        '[class*="char-item"]',
    )
    for selector in row_selectors:
        for row in section.select(selector):
            name_node = row.select_one('[class*="name"], [class*="title"], [class*="label"]')
            value_node = row.select_one('[class*="value"], [class*="val"]')
            if name_node and value_node and name_node is not value_node:
                _add_param(
                    params,
                    name_node.get_text(' ', strip=True),
                    value_node.get_text(' ', strip=True),
                )

    for heading in section.find_all(['h3', 'h4', 'h5']):
        title = clean_text(heading.get_text(' ', strip=True))
        if not title or title.lower() in {'технические характеристики', 'характеристики'}:
            continue

        list_node = heading.find_next_sibling(['ul', 'ol'])
        if list_node is None and heading.parent is not section:
            list_node = heading.parent.find(['ul', 'ol'])
        if list_node:
            items = [clean_text(item.get_text(' ', strip=True)) for item in list_node.find_all('li')]
            items = [item for item in items if item]
            if items:
                _add_param(params, title, '; '.join(items))

    # Some product templates use only simple two-column divs and no tables/dl.
    # Use this broad fallback only when the structured parsers above found nothing;
    # otherwise parent wrappers and drawing links turn into false characteristics.
    if not params:
        for node in section.find_all(['div', 'li']):
            children = [
                child
                for child in node.find_all(recursive=False)
                if isinstance(child, Tag)
            ]
            if not (2 <= len(children) <= 3):
                continue

            texts = [clean_text(child.get_text(' ', strip=True)) for child in children]
            texts = [text for text in texts if text]
            if len(texts) < 2:
                continue

            key, value = texts[0], texts[-1]
            if len(key) <= 80 and len(value) <= 500:
                _add_param(params, key, value)

    return params


def _description_section_text(section) -> list[str]:
    parts: list[str] = []
    paragraphs = section.find_all('p')
    if paragraphs:
        for paragraph in paragraphs:
            text = clean_text(paragraph.get_text(' ', strip=True))
            if len(text) >= 20:
                _append_unique(parts, text)
        return parts

    text = clean_text(section.get_text(' ', strip=True))
    text = re.sub(r'^Описание\s*', '', text, flags=re.I)
    if len(text) >= 20:
        _append_unique(parts, text)
    return parts


def extract_description(soup: BeautifulSoup, container) -> str:
    parts: list[str] = []

    for node in soup.select('.detail-description, [itemprop="description"]'):
        text = clean_text(node.get_text(' ', strip=True))
        if len(text) >= 20:
            _append_unique(parts, text)

    explicit_description_found = False
    for selector in ('#description', '#desc', '[data-section="description"]'):
        section = soup.select_one(selector)
        if section:
            explicit_description_found = True
            for text in _description_section_text(section):
                _append_unique(parts, text)
            break

    if not explicit_description_found:
        heading = None
        for candidate in soup.find_all(['h2', 'h3', 'h4', 'h5']):
            if clean_text(candidate.get_text(' ', strip=True)).lower() == 'описание':
                heading = candidate
                break

        if heading:
            sibling = heading.find_next_sibling()
            while sibling:
                if isinstance(sibling, Tag) and sibling.name in {'h2', 'h3', 'h4', 'h5'}:
                    break
                if isinstance(sibling, Tag):
                    text = clean_text(sibling.get_text(' ', strip=True))
                    if len(text) >= 20:
                        _append_unique(parts, text)
                sibling = sibling.find_next_sibling()

    if not parts:
        for paragraph in container.find_all('p'):
            text = clean_text(paragraph.get_text(' ', strip=True))
            if len(text) >= 30:
                _append_unique(parts, text)

    if not parts:
        meta = soup.find('meta', attrs={'name': 'description'})
        if meta and meta.get('content'):
            _append_unique(parts, meta['content'])

    return ' '.join(parts)[:5000]


def extract_additional_characteristics_text(soup: BeautifulSoup) -> str:
    """Extract the content block displayed below technical characteristics (#text2)."""
    section = soup.select_one('#text2 .text-content, #text2')
    if not section:
        return ''

    parts: list[str] = []
    paragraphs = section.find_all('p')
    if paragraphs:
        for paragraph in paragraphs:
            text = clean_text(paragraph.get_text(' ', strip=True))
            if text:
                parts.append(text)
    else:
        text = clean_text(section.get_text(' ', strip=True))
        if text:
            parts.append(text)

    return ' '.join(parts)[:8000]


def compose_description(
    base_description: str,
    params: dict[str, str],
    additional_text: str,
    *,
    include_characteristics: bool,
) -> str:
    if not include_characteristics:
        return clean_text(base_description)[:12000]

    parts: list[str] = []
    base = clean_text(base_description)
    if base:
        parts.append(base)

    technical_pairs = [
        f'{clean_text(key)}: {clean_text(value)}'
        for key, value in params.items()
        if clean_text(key) and clean_text(value) and clean_text(key) != 'Модификация'
    ]
    if technical_pairs:
        parts.append('Технические характеристики: ' + '; '.join(technical_pairs) + '.')

    extra = clean_text(additional_text)
    if extra:
        parts.append(extra)

    return clean_text(' '.join(parts))[:12000]


def _decode_js_text(value: str) -> str:
    value = value.replace("\\'", "'").replace('\\/', '/').replace('\\\\', '\\')
    return clean_text(html_lib.unescape(value))


_OFFER_HEAD_RE = re.compile(
    r"\{\s*'ID':'(?P<id>\d+)'\s*,\s*"
    r"'NAME':'(?P<name>(?:\\.|[^'])*)'\s*,\s*"
    r"'NAME_HTML':'(?:\\.|[^'])*'\s*,\s*"
    r"'ARTICLE':'(?P<article>(?:\\.|[^'])*)'\s*,\s*"
    r"'ARTICLE_HTML':'(?:\\.|[^'])*'\s*,\s*"
    r"'DETAIL_PAGE_URL':'(?P<detail>(?:\\.|[^'])*)'",
    re.S,
)

_SKU_LIST_CHARS_RE = re.compile(
    r"'SKU_LIST_CHARS'\s*:\s*\[(?P<body>.*?)\]",
    re.S,
)

_SKU_CHAR_RE = re.compile(
    r"\{\s*'NAME':'(?P<name>(?:\\.|[^'])*)'\s*,\s*"
    r"'VALUE':'(?P<value>(?:\\.|[^'])*)'"
    r"(?:\s*,\s*'HINT':'(?:\\.|[^'])*')?\s*\}",
    re.S,
)


def extract_variant_refs(page_html: str, product_url: str) -> list[dict]:
    """Extract Bitrix SKU/offer variants belonging to the current product only."""
    soup = BeautifulSoup(page_html, 'html.parser')
    h1 = soup.find('h1')
    base_name = clean_text(h1.get_text(' ', strip=True) if h1 else '')
    base_path = urlparse(product_url).path.rstrip('/') + '/'

    matches = list(_OFFER_HEAD_RE.finditer(page_html))
    refs: list[dict] = []
    seen_ids: set[str] = set()

    for match_index, match in enumerate(matches):
        offer_id_value = match.group('id')
        if offer_id_value in seen_ids:
            continue

        detail = _decode_js_text(match.group('detail'))
        absolute_url = canonical_url(urljoin(BASE_URL, detail))
        parsed = urlparse(absolute_url)
        offer_path = parsed.path.rstrip('/') + '/'
        if offer_path != base_path:
            continue
        if parse_qs(parsed.query).get('oID') != [offer_id_value]:
            continue

        name = _decode_js_text(match.group('name'))
        sku = _decode_js_text(match.group('article'))
        variant = ''
        if base_name and name.lower().startswith(base_name.lower()):
            variant = clean_text(name[len(base_name):])
        if not variant:
            variant = f'oID {offer_id_value}'

        segment_end = (
            matches[match_index + 1].start()
            if match_index + 1 < len(matches)
            else min(len(page_html), match.start() + 60000)
        )
        segment = page_html[match.start():segment_end]
        chars: dict[str, str] = {}
        chars_block = _SKU_LIST_CHARS_RE.search(segment)
        if chars_block:
            for char_match in _SKU_CHAR_RE.finditer(chars_block.group('body')):
                char_name = _decode_js_text(char_match.group('name'))
                char_value = _decode_js_text(char_match.group('value'))
                if char_name and char_value:
                    chars[char_name] = char_value

        ref = {
            'id': offer_id_value,
            'name': name,
            'sku': sku,
            'url': absolute_url,
            'variant': variant,
        }
        if chars:
            ref['chars'] = chars
        refs.append(ref)
        seen_ids.add(offer_id_value)

    return refs


def parse_product(page_html: str, url: str, category_hint: str | None = None) -> dict:
    soup = BeautifulSoup(page_html, 'html.parser')
    h1 = soup.find('h1')
    name = clean_text(h1.get_text(' ', strip=True) if h1 else '')
    if not name:
        raise ValueError(f'Нет H1 у товара {url}')

    container = find_detail_container(soup)
    text = clean_text(container.get_text(' ', strip=True))
    full_text = clean_text(soup.get_text(' ', strip=True))
    sku = extract_sku(soup, container)

    price_node = soup.select_one('#actual_price')
    price_text = clean_text(price_node.get_text(' ', strip=True)) if price_node else full_text

    retail_pattern = r'Розничная\s+стоимость\s*([0-9\s\xa0]+(?:[.,][0-9]+)?)\s*₽'
    retail_match = re.search(retail_pattern, price_text, flags=re.I)
    if retail_match is None and price_text != full_text:
        retail_match = re.search(retail_pattern, full_text, flags=re.I)
    price = money_to_float(retail_match.group(1)) if retail_match else None

    if price is None:
        any_price = re.search(r'([0-9][0-9\s\xa0]*(?:[.,][0-9]+)?)\s*₽', text)
        price = money_to_float(any_price.group(1)) if any_price else 0.0

    bulk_pattern = (
        r'([0-9\s\xa0]+(?:[.,][0-9]+)?)\s*₽\s*'
        r'при\s+заказе\s+от\s*([0-9\s\xa0]+)\s*шт'
    )
    bulk_match = re.search(bulk_pattern, price_text, flags=re.I)
    if bulk_match is None:
        retail_anchor = re.search(r'Розничная\s+стоимость', full_text, flags=re.I)
        bulk_scope = (
            full_text[retail_anchor.start():retail_anchor.start() + 1000]
            if retail_anchor
            else text
        )
        bulk_match = re.search(bulk_pattern, bulk_scope, flags=re.I)

    bulk_price = money_to_float(bulk_match.group(1)) if bulk_match else None
    bulk_min_qty = int(re.sub(r'\D', '', bulk_match.group(2))) if bulk_match else None

    stock_match = re.search(r'В\s+наличии\s*:?\s*(\d+)', text, flags=re.I)
    stock = int(stock_match.group(1)) if stock_match else None

    params = extract_params(soup, container)
    base_description = extract_description(soup, container)
    additional_characteristics = extract_additional_characteristics_text(soup)
    description = compose_description(
        base_description,
        params,
        additional_characteristics,
        include_characteristics=bool(additional_characteristics),
    )

    return {
        'url': url,
        'name': name,
        'sku': sku,
        'category': detect_category(soup, category_hint),
        'price': float(price or 0),
        'bulk_price': bulk_price,
        'bulk_min_qty': bulk_min_qty,
        'stock': stock,
        'description': description,
        'pictures': extract_pictures(soup, container),
        'params': params,
        '_base_description': base_description,
        '_additional_characteristics': additional_characteristics,
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

    candidate = (
        f"{candidate[:65]}-"
        f"{hashlib.sha1(product['url'].encode('utf-8')).hexdigest()[:10]}"
    )
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

    category_names = sorted({clean_text(product.get('category')) or 'Каталог' for product in products})
    categories = ET.SubElement(shop, 'categories')
    category_ids: dict[str, str] = {}
    for name in category_names:
        cid = category_id(name)
        category_ids[name] = cid
        ET.SubElement(categories, 'category', {'id': cid}).text = name

    offers = ET.SubElement(shop, 'offers')
    used_ids: set[str] = set()

    for product in sorted(
        products,
        key=lambda item: (
            item.get('category', ''),
            item.get('name', ''),
            item.get('url', ''),
        ),
    ):
        price = float(product.get('price') or 0)
        offer = ET.SubElement(
            offers,
            'offer',
            {
                'id': offer_id(product, used_ids),
                'available': 'true' if price > 0 else 'false',
            },
        )

        ET.SubElement(offer, 'url').text = product['url']
        ET.SubElement(offer, 'price').text = format_money(price)
        ET.SubElement(offer, 'currencyId').text = 'RUR'

        category = clean_text(product.get('category')) or 'Каталог'
        ET.SubElement(offer, 'categoryId').text = category_ids[category]

        for picture in product.get('pictures', [])[:20]:
            ET.SubElement(offer, 'picture').text = picture

        ET.SubElement(offer, 'name').text = product['name']
        ET.SubElement(offer, 'vendor').text = 'Артикул-Мебель'

        if product.get('sku'):
            ET.SubElement(offer, 'vendorCode').text = product['sku']
        if product.get('description'):
            ET.SubElement(offer, 'description').text = product['description']

        for key, value in product.get('params', {}).items():
            if clean_text(key) and clean_text(value):
                ET.SubElement(
                    offer,
                    'param',
                    {'name': clean_text(key)},
                ).text = clean_text(value)

        if product.get('bulk_price') is not None:
            ET.SubElement(offer, 'param', {'name': 'Оптовая цена'}).text = format_money(
                product['bulk_price']
            )
        if product.get('bulk_min_qty') is not None:
            ET.SubElement(
                offer,
                'param',
                {'name': 'Минимальное количество для оптовой цены'},
            ).text = str(product['bulk_min_qty'])
        if product.get('stock') is not None:
            ET.SubElement(offer, 'param', {'name': 'Остаток'}).text = str(product['stock'])
        if price <= 0:
            ET.SubElement(offer, 'param', {'name': 'Цена по запросу'}).text = 'Да'

    ET.indent(root, space='  ')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def load_one(url: str) -> list[dict]:
    try:
        base_html = fetch(url)
        variant_refs = extract_variant_refs(base_html, url)
        if not variant_refs:
            return [parse_product(base_html, url)]

        products: list[dict] = []
        for ref in variant_refs:
            try:
                variant_html = fetch(ref['url'])
                product = parse_product(variant_html, ref['url'])
                product['name'] = ref['name'] or product['name']
                product['sku'] = ref['sku'] or product['sku']
                params = product.setdefault('params', {})
                selected_chars = ref.get('chars') or {}
                params.update(selected_chars)
                product['description'] = compose_description(
                    product.get('_base_description', product.get('description', '')),
                    params,
                    product.get('_additional_characteristics', ''),
                    include_characteristics=bool(
                        selected_chars or product.get('_additional_characteristics')
                    ),
                )
                params['Модификация'] = ref['variant']
                products.append(product)
            except Exception as exc:
                print(f"[variant] skip {ref['url']}: {exc}", file=sys.stderr)

        if not products:
            raise RuntimeError(f'Не удалось разобрать ни одной модификации для {url}')
        return products
    except Exception as exc:
        print(f'[product] skip {url}: {exc}', file=sys.stderr)
        return []


def main() -> None:
    urls = discover_product_urls()
    if len(urls) < MIN_PRODUCTS:
        raise RuntimeError(
            f'Найдено только {len(urls)} ссылок на товары; '
            f'минимум для безопасного обновления {MIN_PRODUCTS}. '
            'Предыдущий YML не будет перезаписан.'
        )

    products: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(load_one, url): url for url in urls}
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            variants = future.result() or []
            products.extend(variants)
            if index % 25 == 0 or index == len(futures):
                print(
                    f'Обработано базовых карточек {index}/{len(futures)}, '
                    f'offers={len(products)}'
                )

    if len(products) < MIN_PRODUCTS:
        raise RuntimeError(
            f'Успешно разобрано только {len(products)} offers; '
            f'минимум {MIN_PRODUCTS}. Предыдущий YML не будет перезаписан.'
        )

    data = build_yml(products)
    if len(data) < 10_000:
        raise RuntimeError(f'Сформированный YML подозрительно мал: {len(data)} байт')

    temporary_path = OUTPUT + '.tmp'
    with open(temporary_path, 'wb') as output_file:
        output_file.write(data)
    os.replace(temporary_path, OUTPUT)
    print(f'Готово: {OUTPUT}; offers={len(products)}; размер={len(data)} байт')


if __name__ == '__main__':
    main()
