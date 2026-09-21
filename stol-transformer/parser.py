#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup, Tag
from requests.auth import HTTPBasicAuth

BASE_URL = "https://stol-transformer.ru/showcase/"
HOST = urlparse(BASE_URL).netloc.lower()
DEFAULT_TIMEOUT = 45
USER_AGENT = "Mozilla/5.0 (compatible; Megapolis-StolTransformer-Parser/1.0; +https://github.com/gera5malyov-jpg/norden-yml)"

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
OUT.mkdir(parents=True, exist_ok=True)

PRODUCT_JSON = OUT / "products.json"
PRODUCT_CSV = OUT / "products.csv"
REPORT_JSON = OUT / "report.json"

PRICE_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[\s\u00a0]\d{3})+|\d{4,7})(?:[.,](\d{1,2}))?\s*(?:₽|руб\.?|р\.)", re.I)
SKU_LABEL_RE = re.compile(r"(?:артикул|арт\.?|sku|код\s*(?:товара|модели)?|vendor\s*code)\s*[:№#-]*\s*([A-Za-zА-Яа-я0-9._/\-]+)", re.I)
STOCK_RE = re.compile(r"(?:остат(?:ок|ки)|наличие|в наличии|на складе)\s*[:\-]?\s*([^\n|;]{1,80})", re.I)
PRODUCT_HINT_RE = re.compile(r"(?:товар|product|stol-transformer|стол-трансформер|стул|кресло|стол)", re.I)
WHOLESALE_LABEL_RE = re.compile(r"(?:оптов(?:ая|ой|ую)?\s*цена|опт\.?|закупочн(?:ая|ой|ую)?\s*цена|закупка|дилерск(?:ая|ой)\s*цена)", re.I)
RETAIL_LABEL_RE = re.compile(r"(?:розничн(?:ая|ой|ую)?\s*цена|розница|ррц|цена\s*для\s*(?:покупателя|клиента)|retail)", re.I)
PRICE_CHARACTERISTIC_RE = re.compile(r"(?:цена|опт|закуп|рознич|ррц|price)", re.I)
URL_SKU_RE = re.compile(r"(?:^|[-_/])([A-ZА-Я]{1,12}\d{2,}[A-ZА-Я0-9]*)/?$", re.I)
SKIP_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf", ".zip", ".rar", ".css", ".js", ".xml"}
SKIP_PATH_PARTS = ("/cart", "/basket", "/checkout", "/login", "/logout", "/registration", "/search", "/contacts", "/oplata", "/dostav", "/video")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def clean_url(base: str, href: str | None) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("mailto:", "tel:", "javascript:", "#")):
        return None
    absolute = urljoin(base, href)
    absolute, _ = urldefrag(absolute)
    p = urlparse(absolute)
    if p.scheme not in {"http", "https"} or p.netloc.lower() != HOST:
        return None
    if any(p.path.lower().endswith(ext) for ext in SKIP_EXT):
        return None
    return absolute


def first_nonempty(values: Iterable[str | None]) -> str:
    for value in values:
        value = clean_text(value)
        if value:
            return value
    return ""


def parse_price_text(text: str) -> float | None:
    m = PRICE_RE.search(text or "")
    if not m:
        return None
    whole = re.sub(r"[\s\u00a0]", "", m.group(1))
    frac = m.group(2) or ""
    try:
        return float(whole + (("." + frac) if frac else ""))
    except ValueError:
        return None


def price_to_str(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if value.is_integer() else f"{value:.2f}"


@dataclass
class Product:
    url: str
    name: str
    sku: str = ""
    price: str = ""  # compatibility alias: same value as retail_price
    retail_price: str = ""
    purchase_price: str = ""
    old_price: str = ""
    stock: str = ""
    brand: str = ""
    category: str = ""
    model: str = ""
    color: str = ""
    support_color: str = ""
    description: str = ""
    images: list[str] | None = None
    characteristics: dict[str, str] | None = None
    variants: dict[str, list[str]] | None = None
    raw_prices: list[str] | None = None

    def __post_init__(self) -> None:
        self.images = self.images or []
        self.characteristics = self.characteristics or {}
        self.variants = self.variants or {}
        self.raw_prices = self.raw_prices or []


class Parser:
    def __init__(self, login: str, password: str, delay: float = 0.15, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.login = login
        self.password = password
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5"})
        self.errors: list[dict[str, str]] = []
        self.pages_fetched = 0
        self.auth_mode = "unknown"

    def get(self, url: str, *, allow_auth_retry: bool = True) -> requests.Response:
        response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
        if response.status_code == 401 and allow_auth_retry:
            response = self.session.get(url, auth=HTTPBasicAuth(self.login, self.password), timeout=self.timeout, allow_redirects=True)
            if response.status_code != 401:
                self.session.auth = HTTPBasicAuth(self.login, self.password)
                self.auth_mode = "http_basic"
        response.raise_for_status()
        self.pages_fetched += 1
        if self.delay:
            time.sleep(self.delay)
        return response

    def authenticate(self) -> requests.Response:
        response = self.get(BASE_URL)
        if self.auth_mode == "http_basic":
            return response

        soup = BeautifulSoup(response.text, "lxml")
        password_input = soup.find("input", attrs={"type": re.compile("password", re.I)})
        if not password_input:
            self.auth_mode = "none_or_cookie_already"
            return response

        form = password_input.find_parent("form")
        if not isinstance(form, Tag):
            raise RuntimeError("На странице есть поле пароля, но форма авторизации не найдена")

        payload: dict[str, str] = {}
        user_field = None
        pass_field = password_input.get("name")
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            typ = str(inp.get("type", "text")).lower()
            val = str(inp.get("value", ""))
            if typ in {"hidden", "submit"}:
                payload[name] = val
            if typ in {"text", "email"} and (not user_field or re.search(r"login|user|email|name", name, re.I)):
                user_field = name
        if not user_field or not pass_field:
            raise RuntimeError("Не удалось определить поля логина/пароля в форме")

        payload[user_field] = self.login
        payload[str(pass_field)] = self.password
        action = urljoin(response.url, str(form.get("action") or response.url))
        method = str(form.get("method") or "post").lower()
        if method == "get":
            login_response = self.session.get(action, params=payload, timeout=self.timeout, allow_redirects=True)
        else:
            login_response = self.session.post(action, data=payload, timeout=self.timeout, allow_redirects=True)
        login_response.raise_for_status()
        self.pages_fetched += 1
        if BeautifulSoup(login_response.text, "lxml").find("input", attrs={"type": re.compile("password", re.I)}):
            raise RuntimeError("Авторизация через форму не подтверждена: форма логина осталась на странице")
        self.auth_mode = "html_form"
        return login_response

    def extract_jsonld_products(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        out: list[Product] = []
        for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
            raw = script.string or script.get_text(" ", strip=True)
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            stack = data if isinstance(data, list) else [data]
            expanded: list[Any] = []
            for obj in stack:
                if isinstance(obj, dict) and isinstance(obj.get("@graph"), list):
                    expanded.extend(obj["@graph"])
                expanded.append(obj)
            for obj in expanded:
                if not isinstance(obj, dict) or str(obj.get("@type", "")).lower() != "product":
                    continue
                offers = obj.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                image = obj.get("image") or []
                images = image if isinstance(image, list) else [image]
                brand = obj.get("brand") or ""
                if isinstance(brand, dict):
                    brand = brand.get("name", "")
                price = ""
                stock = ""
                if isinstance(offers, dict):
                    price = clean_text(str(offers.get("price") or offers.get("lowPrice") or ""))
                    stock = clean_text(str(offers.get("availability") or ""))
                out.append(Product(
                    url=str(obj.get("url") or page_url),
                    name=clean_text(str(obj.get("name") or "")),
                    sku=clean_text(str(obj.get("sku") or obj.get("mpn") or "")),
                    price=price,
                    retail_price=price,
                    stock=stock,
                    brand=clean_text(str(brand)),
                    description=clean_text(str(obj.get("description") or "")),
                    images=[urljoin(page_url, str(x)) for x in images if x],
                ))
        return [p for p in out if p.name]

    def extract_characteristics(self, soup: BeautifulSoup) -> dict[str, str]:
        result: dict[str, str] = {}

        def store(key: str, value: str) -> None:
            key, value = clean_text(key).rstrip(":"), clean_text(value)
            if not key or not value or len(key) > 100:
                return
            # Цены — отдельные коммерческие поля, а не характеристики товара.
            # Особенно purchase_price/оптовую цену никогда не выгружаем как характеристику.
            if PRICE_CHARACTERISTIC_RE.search(key):
                return
            result.setdefault(key, value)

        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = [clean_text(c.get_text(" ", strip=True)) for c in row.find_all(["th", "td"])]
                if len(cells) >= 2:
                    store(cells[0], cells[1])
        for dl in soup.find_all("dl"):
            for dt in dl.find_all("dt"):
                dd = dt.find_next_sibling("dd")
                if dd:
                    store(dt.get_text(" ", strip=True), dd.get_text(" ", strip=True))
        for node in soup.select(".characteristics li, .chars li, .properties li, .specifications li, [class*='character'] li, [class*='property'] li"):
            text = clean_text(node.get_text(" ", strip=True))
            if ":" in text:
                k, v = map(clean_text, text.split(":", 1))
                store(k, v)

        # Частый шаблон защищённых каталогов: отдельные label/value div.
        for row in soup.select("[class*='character'] [class*='row'], [class*='property'] [class*='row'], [class*='spec'] [class*='row']"):
            cells = [clean_text(x.get_text(" ", strip=True)) for x in row.find_all(recursive=False)]
            cells = [x for x in cells if x]
            if len(cells) >= 2:
                store(cells[0], cells[1])
        return result

    @staticmethod
    def sku_from_url(page_url: str) -> str:
        slug = urlparse(page_url).path.rstrip("/")
        m = URL_SKU_RE.search(slug)
        return clean_text(m.group(1)) if m else ""

    @staticmethod
    def characteristic(chars: dict[str, str], *patterns: str) -> str:
        for key, value in chars.items():
            if any(re.search(pattern, key, re.I) for pattern in patterns):
                return clean_text(value)
        return ""

    def extract_price_fields(self, soup: BeautifulSoup) -> tuple[str, str, list[str]]:
        """Return (retail_price, purchase_price, raw_prices).

        Wholesale/purchase price is intentionally kept outside characteristics.
        """
        candidates: list[tuple[str, float]] = []

        # Prefer compact containers/rows: label + amount are usually siblings.
        selectors = "tr, li, dt, dd, [class*='price'], [class*='cost'], [class*='row'], [class*='field']"
        seen_text: set[str] = set()
        for node in soup.select(selectors):
            text = clean_text(node.get_text(" ", strip=True))
            if not text or text in seen_text or len(text) > 500:
                continue
            seen_text.add(text)
            for m in PRICE_RE.finditer(text):
                value = parse_price_text(m.group(0))
                if value is not None:
                    candidates.append((text, value))

        full_text = clean_text(soup.get_text("\n", strip=True))
        raw_prices = list(dict.fromkeys(clean_text(m.group(0)) for m in PRICE_RE.finditer(full_text)))[:50]

        retail = ""
        purchase = ""
        for text, value in candidates:
            if not purchase and WHOLESALE_LABEL_RE.search(text):
                purchase = price_to_str(value)
            if not retail and RETAIL_LABEL_RE.search(text):
                retail = price_to_str(value)

        # Regex fallback for label followed by the amount in the page text.
        def labeled(pattern: re.Pattern[str]) -> str:
            m = re.search(pattern.pattern + r"[^\d]{0,80}" + PRICE_RE.pattern, full_text, re.I)
            if not m:
                return ""
            pm = PRICE_RE.search(m.group(0))
            return price_to_str(parse_price_text(pm.group(0))) if pm else ""

        purchase = purchase or labeled(WHOLESALE_LABEL_RE)
        retail = retail or labeled(RETAIL_LABEL_RE)

        # Structured retail price (public/RRP) is safe as fallback only for retail.
        if not retail:
            meta_price = soup.select_one("meta[itemprop='price']")
            if meta_price and meta_price.get("content"):
                retail = clean_text(str(meta_price.get("content")))

        return retail, purchase, raw_prices

    def extract_variants(self, soup: BeautifulSoup) -> dict[str, list[str]]:
        variants: dict[str, list[str]] = {}
        for select in soup.find_all("select"):
            key = clean_text(str(select.get("name") or select.get("id") or "Вариант"))
            values = []
            for opt in select.find_all("option"):
                value = clean_text(opt.get_text(" ", strip=True))
                if value and value.lower() not in {"выберите", "select", "-"}:
                    values.append(value)
            if values:
                variants[key] = list(dict.fromkeys(values))
        for group in soup.select("[class*='color'], [class*='variant'], [class*='option']"):
            text = clean_text(group.get_text(" ", strip=True))
            if not text or len(text) > 1200:
                continue
            prev = group.find_previous(["h2", "h3", "h4", "strong", "b"])
            heading = first_nonempty([
                group.get("data-name"),
                prev.get_text(" ", strip=True) if prev else "",
            ])
            labels = [clean_text(x.get_text(" ", strip=True)) for x in group.find_all("label")]
            labels = [x for x in labels if x and len(x) <= 80]
            if labels:
                key = heading if heading and len(heading) <= 80 else "Варианты"
                variants.setdefault(key, [])
                variants[key] = list(dict.fromkeys(variants[key] + labels))
        return variants

    def extract_images(self, soup: BeautifulSoup, page_url: str) -> list[str]:
        images: list[str] = []

        def add(value: str | None) -> None:
            if not value:
                return
            value = str(value).strip()
            if not value or value.startswith("data:"):
                return
            # srcset: берём все реальные URL, затем дедуплицируем.
            if "," in value and (" " in value or "w," in value):
                for part in value.split(","):
                    add(part.strip().split(" ")[0])
                return
            absolute = urljoin(page_url, value)
            low = absolute.lower()
            if any(x in low for x in ("logo", "favicon", "sprite", "icon-", "/icons/")):
                return
            images.append(absolute)

        for meta in soup.select("meta[property='og:image'], meta[name='twitter:image']"):
            add(meta.get("content"))

        # Только медиа внутри карточки/галереи конкретной модификации.
        selectors = "[itemprop='image'], [class*='product'] img, [class*='gallery'] img, [class*='slider'] img, [class*='photo'] img"
        for img in soup.select(selectors):
            for attr in ("data-large", "data-zoom", "data-image", "data-full", "data-src", "data-original", "data-lazy", "srcset", "src"):
                add(img.get(attr))

        # В галереях оригинал часто лежит в href у <a>, а превью — в <img>.
        for a in soup.select("[class*='product'] a[href], [class*='gallery'] a[href], [class*='slider'] a[href], [class*='photo'] a[href]"):
            href = str(a.get("href") or "")
            if re.search(r"\.(?:jpe?g|png|webp|gif)(?:\?|$)", href, re.I):
                add(href)

        # CSS background-image / data-* контейнеров.
        for node in soup.select("[class*='product'], [class*='gallery'], [class*='slider'], [class*='photo']"):
            style = str(node.get("style") or "")
            for m in re.finditer(r"url\((?:['\"])?([^)'\"]+)", style, re.I):
                add(m.group(1))
            for attr in ("data-image", "data-src", "data-large", "data-full", "data-zoom"):
                add(node.get(attr))

        return list(dict.fromkeys(images))[:120]

    def extract_product(self, html: str, page_url: str, category_hint: str = "") -> Product | None:
        soup = BeautifulSoup(html, "lxml")
        jsonld = self.extract_jsonld_products(soup, page_url)
        h1 = soup.find("h1")
        item_name = soup.select_one("[itemprop='name']")
        title = first_nonempty([
            h1.get_text(" ", strip=True) if h1 else "",
            item_name.get_text(" ", strip=True) if item_name else "",
            jsonld[0].name if jsonld else "",
        ])
        if not title:
            return None

        full_text = clean_text(soup.get_text("\n", strip=True))
        retail_price, purchase_price, prices = self.extract_price_fields(soup)

        if not retail_price and jsonld and jsonld[0].retail_price:
            retail_price = jsonld[0].retail_price
        if not retail_price and prices:
            # Только последний fallback: первая найденная цена считается розничной,
            # если на странице нет явной пометки "оптовая/закупочная".
            first_price = parse_price_text(prices[0])
            if first_price is not None and not WHOLESALE_LABEL_RE.search(full_text):
                retail_price = price_to_str(first_price)

        price = retail_price

        old_price = ""
        for node in soup.select("[class*='old-price'], [class*='old_price'], [class*='price-old'], del, s"):
            txt = clean_text(node.get_text(" ", strip=True))
            v = parse_price_text(txt)
            if v is not None:
                old_price = price_to_str(v)
                break

        sku_match = SKU_LABEL_RE.search(full_text)
        sku = sku_match.group(1) if sku_match else ""
        if not sku and jsonld:
            sku = jsonld[0].sku
        if not sku:
            sku = self.sku_from_url(page_url)

        chars = self.extract_characteristics(soup)
        if not sku:
            for k, v in chars.items():
                if re.search(r"артикул|sku|код", k, re.I):
                    sku = v
                    break

        stock_match = STOCK_RE.search(full_text)
        stock = clean_text(stock_match.group(1)) if stock_match else ""
        brand = ""
        for k, v in chars.items():
            if re.search(r"производитель|бренд", k, re.I):
                brand = v
                break
        if not brand and jsonld:
            brand = jsonld[0].brand

        color = self.characteristic(chars, r"^цвет$", r"цвет.*столеш", r"декор")
        support_color = self.characteristic(chars, r"цвет.*опор", r"цвет.*нож")
        model = self.characteristic(chars, r"^модель$", r"серия", r"коллекц")
        if not model:
            # Для примера "... LEVMAR Accord R D95S53 Белый мрамор ...":
            # базовая модель остаётся в name, а цвет/артикул сохраняются отдельными полями.
            model = re.sub(r"\s*\[[^\]]+\]\s*$", "", title).strip()

        desc_node = soup.select_one("[itemprop='description'], .description, .product-description, [class*='description']")
        description = clean_text(desc_node.get_text("\n", strip=True)) if desc_node else ""
        if not description:
            md = soup.find("meta", attrs={"name": "description"})
            description = clean_text(str(md.get("content") or "")) if md else ""

        images = self.extract_images(soup, page_url)
        variants = self.extract_variants(soup)

        path = urlparse(page_url).path
        confidence = bool(sku or (price and (PRODUCT_HINT_RE.search(title) or PRODUCT_HINT_RE.search(path))))
        if not confidence and jsonld:
            confidence = True
        if not confidence:
            return None

        return Product(
            url=page_url,
            name=title,
            sku=clean_text(sku),
            price=clean_text(price),
            retail_price=clean_text(retail_price),
            purchase_price=clean_text(purchase_price),
            old_price=clean_text(old_price),
            stock=stock,
            brand=brand,
            category=category_hint,
            model=model,
            color=color,
            support_color=support_color,
            description=description,
            images=images,
            characteristics=chars,
            variants=variants,
            raw_prices=prices,
        )

    def candidate_links(self, soup: BeautifulSoup, page_url: str) -> tuple[list[str], list[str]]:
        crawl: list[str] = []
        products: list[str] = []
        for a in soup.find_all("a", href=True):
            url = clean_url(page_url, str(a.get("href")))
            if not url:
                continue
            path = urlparse(url).path.lower()
            if any(part in path for part in SKIP_PATH_PARTS):
                continue
            text = clean_text(a.get_text(" ", strip=True))
            classes = " ".join(a.get("class") or [])
            parent_classes = " ".join(a.parent.get("class") or []) if isinstance(a.parent, Tag) else ""
            is_productish = bool(
                PRODUCT_HINT_RE.search(text)
                or PRODUCT_HINT_RE.search(path)
                or re.search(r"product|item|card|offer|good", classes + " " + parent_classes, re.I)
            )
            if path.startswith("/showcase"):
                crawl.append(url)
                if is_productish and path.rstrip("/") != "/showcase":
                    products.append(url)
            elif is_productish:
                products.append(url)
        return list(dict.fromkeys(crawl)), list(dict.fromkeys(products))

    def parse_card_products(self, soup: BeautifulSoup, page_url: str) -> list[Product]:
        products: list[Product] = []
        card_selectors = [
            "[itemtype*='Product']", ".product", ".product-card", ".catalog-item", ".shop-item",
            "[class*='product-card']", "[class*='catalog-item']", "[class*='product_item']"
        ]
        seen_nodes: set[int] = set()
        for selector in card_selectors:
            for card in soup.select(selector):
                ident = id(card)
                if ident in seen_nodes:
                    continue
                seen_nodes.add(ident)
                a = card.find("a", href=True)
                title_node = card.find(["h2", "h3", "h4"]) or card.select_one("[itemprop='name']") or a
                name = clean_text(title_node.get_text(" ", strip=True)) if title_node else ""
                if not name:
                    continue
                url = clean_url(page_url, str(a.get("href"))) if a else page_url
                if not url:
                    url = page_url
                text = clean_text(card.get_text(" ", strip=True))
                sku_match = SKU_LABEL_RE.search(text)
                sku = sku_match.group(1) if sku_match else self.sku_from_url(url)
                stock_match = STOCK_RE.search(text)

                card_prices = [clean_text(m.group(0)) for m in PRICE_RE.finditer(text)]
                retail_price = ""
                purchase_price = ""
                for raw in card_prices:
                    value = parse_price_text(raw)
                    if value is None:
                        continue
                    pos = text.find(raw)
                    context = text[max(0, pos - 100):pos + len(raw) + 30]
                    if not purchase_price and WHOLESALE_LABEL_RE.search(context):
                        purchase_price = price_to_str(value)
                    if not retail_price and RETAIL_LABEL_RE.search(context):
                        retail_price = price_to_str(value)
                if not retail_price and card_prices and not WHOLESALE_LABEL_RE.search(text):
                    value = parse_price_text(card_prices[0])
                    retail_price = price_to_str(value) if value is not None else ""

                images = []
                img = card.find("img")
                if img:
                    src = img.get("data-src") or img.get("src")
                    if src:
                        images = [urljoin(page_url, str(src))]
                if retail_price or purchase_price or sku:
                    products.append(Product(
                        url=url,
                        name=name,
                        sku=sku,
                        price=retail_price,
                        retail_price=retail_price,
                        purchase_price=purchase_price,
                        stock=clean_text(stock_match.group(1)) if stock_match else "",
                        images=images,
                        raw_prices=card_prices[:20],
                    ))
        return products

    def crawl(self, max_pages: int = 2500) -> list[Product]:
        first = self.authenticate()
        queue = deque([first.url])
        prefetched = {first.url: first.text}
        seen: set[str] = set()
        product_urls: set[str] = set()
        card_products: list[Product] = []
        page_categories: dict[str, str] = {}

        while queue and len(seen) < max_pages:
            url = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            try:
                html = prefetched.pop(url, None)
                if html is None:
                    html = self.get(url).text
                soup = BeautifulSoup(html, "lxml")
                card_products.extend(self.parse_card_products(soup, url))
                crawl_links, product_links = self.candidate_links(soup, url)
                product_urls.update(product_links)
                for link in crawl_links:
                    if link not in seen:
                        queue.append(link)
                heading = soup.find("h1")
                cat = clean_text(heading.get_text(" ", strip=True)) if heading else ""
                for link in product_links:
                    if cat:
                        page_categories.setdefault(link, cat)
            except Exception as exc:
                self.errors.append({"url": url, "stage": "catalog", "message": str(exc)[:500]})

        product_urls.add(first.url)
        detailed: list[Product] = []
        for url in sorted(product_urls):
            try:
                html = prefetched.pop(url, None)
                if html is None:
                    html = self.get(url).text
                product = self.extract_product(html, url, page_categories.get(url, ""))
                if product:
                    detailed.append(product)
            except Exception as exc:
                self.errors.append({"url": url, "stage": "product", "message": str(exc)[:500]})

        merged: dict[str, Product] = {}
        for p in card_products + detailed:
            key = p.sku.lower() if p.sku else p.url.lower()
            if key not in merged:
                merged[key] = p
                continue
            old = merged[key]
            for field in ("name", "sku", "price", "retail_price", "purchase_price", "old_price", "stock", "brand", "category", "model", "color", "support_color", "description"):
                if not getattr(old, field) and getattr(p, field):
                    setattr(old, field, getattr(p, field))
            old.images = list(dict.fromkeys((old.images or []) + (p.images or [])))
            old.raw_prices = list(dict.fromkeys((old.raw_prices or []) + (p.raw_prices or [])))
            old.characteristics = {**(old.characteristics or {}), **(p.characteristics or {})}
            vv = dict(old.variants or {})
            for k, vals in (p.variants or {}).items():
                vv[k] = list(dict.fromkeys(vv.get(k, []) + vals))
            old.variants = vv

        products = list(merged.values())
        products.sort(key=lambda p: (p.sku or "~", p.name, p.url))
        return products


def save(products: list[Product], parser: Parser) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "source": BASE_URL,
        "generated_at": generated_at,
        "auth_mode": parser.auth_mode,
        "count": len(products),
        "products": [asdict(p) for p in products],
    }
    PRODUCT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    fields = ["sku", "name", "model", "color", "support_color", "retail_price", "purchase_price", "old_price", "stock", "brand", "category", "url", "description", "images", "characteristics", "variants"]
    with PRODUCT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        writer.writeheader()
        for p in products:
            writer.writerow({
                "sku": p.sku,
                "name": p.name,
                "model": p.model,
                "color": p.color,
                "support_color": p.support_color,
                "retail_price": p.retail_price,
                "purchase_price": p.purchase_price,
                "old_price": p.old_price,
                "stock": p.stock,
                "brand": p.brand,
                "category": p.category,
                "url": p.url,
                "description": p.description,
                "images": " | ".join(p.images or []),
                "characteristics": json.dumps(p.characteristics or {}, ensure_ascii=False),
                "variants": json.dumps(p.variants or {}, ensure_ascii=False),
            })

    report = {
        "status": "ok" if products else "error",
        "source": BASE_URL,
        "generated_at": generated_at,
        "auth_mode": parser.auth_mode,
        "products": len(products),
        "with_sku": sum(bool(p.sku) for p in products),
        "with_retail_price": sum(bool(p.retail_price) for p in products),
        "with_purchase_price": sum(bool(p.purchase_price) for p in products),
        "with_color": sum(bool(p.color) for p in products),
        "with_stock": sum(bool(p.stock) for p in products),
        "with_images": sum(bool(p.images) for p in products),
        "pages_fetched": parser.pages_fetched,
        "errors_count": len(parser.errors),
        "errors": parser.errors[:200],
    }
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Parser for protected stol-transformer.ru showcase catalog")
    ap.add_argument("--max-pages", type=int, default=int(os.getenv("STOL_TRANSFORMER_MAX_PAGES", "2500")))
    ap.add_argument("--delay", type=float, default=float(os.getenv("STOL_TRANSFORMER_DELAY", "0.15")))
    args = ap.parse_args()

    login = os.getenv("STOL_TRANSFORMER_LOGIN", "").strip()
    password = os.getenv("STOL_TRANSFORMER_PASSWORD", "").strip()
    if not login or not password:
        print("ERROR: set STOL_TRANSFORMER_LOGIN and STOL_TRANSFORMER_PASSWORD", file=sys.stderr)
        return 2

    parser = Parser(login, password, delay=max(args.delay, 0.0))
    try:
        products = parser.crawl(max_pages=max(1, args.max_pages))
        report = save(products, parser)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not products:
            return 3
        return 0
    except Exception as exc:
        parser.errors.append({"url": BASE_URL, "stage": "fatal", "message": str(exc)[:1000]})
        report = save([], parser)
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
