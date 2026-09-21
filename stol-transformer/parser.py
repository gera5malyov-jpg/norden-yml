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
    price: str = ""
    old_price: str = ""
    stock: str = ""
    brand: str = ""
    category: str = ""
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
                    stock=stock,
                    brand=clean_text(str(brand)),
                    description=clean_text(str(obj.get("description") or "")),
                    images=[urljoin(page_url, str(x)) for x in images if x],
                ))
        return [p for p in out if p.name]

    def extract_characteristics(self, soup: BeautifulSoup) -> dict[str, str]:
        result: dict[str, str] = {}
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = [clean_text(c.get_text(" ", strip=True)) for c in row.find_all(["th", "td"])]
                if len(cells) >= 2 and cells[0] and cells[1] and len(cells[0]) <= 100:
                    result.setdefault(cells[0].rstrip(":"), cells[1])
        for dl in soup.find_all("dl"):
            for dt in dl.find_all("dt"):
                dd = dt.find_next_sibling("dd")
                if dd:
                    k, v = clean_text(dt.get_text(" ", strip=True)), clean_text(dd.get_text(" ", strip=True))
                    if k and v:
                        result.setdefault(k.rstrip(":"), v)
        for node in soup.select(".characteristics li, .chars li, .properties li, .specifications li, [class*='character'] li, [class*='property'] li"):
            text = clean_text(node.get_text(" ", strip=True))
            if ":" in text:
                k, v = map(clean_text, text.split(":", 1))
                if k and v and len(k) <= 100:
                    result.setdefault(k, v)
        return result

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
        for meta in soup.select("meta[property='og:image'], meta[name='twitter:image']"):
            u = meta.get("content")
            if u:
                images.append(urljoin(page_url, str(u)))
        selectors = "main img, article img, [class*='product'] img, [class*='gallery'] img, [class*='slider'] img"
        for img in soup.select(selectors):
            for attr in ("data-src", "data-original", "data-lazy", "src"):
                u = img.get(attr)
                if u and not str(u).startswith("data:"):
                    images.append(urljoin(page_url, str(u)))
                    break
        return list(dict.fromkeys(images))[:80]

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
        prices: list[str] = []
        for node in soup.select("[itemprop='price'], [class*='price'], [id*='price']"):
            txt = clean_text(str(node.get("content") or node.get_text(" ", strip=True)))
            if txt and parse_price_text(txt) is not None:
                prices.append(txt)
        for m in PRICE_RE.finditer(full_text):
            prices.append(clean_text(m.group(0)))
        prices = list(dict.fromkeys(prices))[:20]

        price = ""
        meta_price = soup.select_one("meta[itemprop='price']")
        if meta_price and meta_price.get("content"):
            price = clean_text(str(meta_price.get("content")))
        elif jsonld and jsonld[0].price:
            price = jsonld[0].price
        elif prices:
            v = parse_price_text(prices[0])
            price = price_to_str(v) if v is not None else prices[0]

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
            old_price=clean_text(old_price),
            stock=stock,
            brand=brand,
            category=category_hint,
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
                pval = parse_price_text(text)
                sku_match = SKU_LABEL_RE.search(text)
                stock_match = STOCK_RE.search(text)
                images = []
                img = card.find("img")
                if img:
                    src = img.get("data-src") or img.get("src")
                    if src:
                        images = [urljoin(page_url, str(src))]
                if pval is not None or sku_match:
                    products.append(Product(
                        url=url,
                        name=name,
                        sku=sku_match.group(1) if sku_match else "",
                        price=price_to_str(pval) if pval is not None else "",
                        stock=clean_text(stock_match.group(1)) if stock_match else "",
                        images=images,
                        raw_prices=[m.group(0) for m in PRICE_RE.finditer(text)][:10],
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
            for field in ("name", "sku", "price", "old_price", "stock", "brand", "category", "description"):
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

    fields = ["sku", "name", "price", "old_price", "stock", "brand", "category", "url", "description", "images", "characteristics", "variants"]
    with PRODUCT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        writer.writeheader()
        for p in products:
            writer.writerow({
                "sku": p.sku,
                "name": p.name,
                "price": p.price,
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
        "with_price": sum(bool(p.price) for p in products),
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
