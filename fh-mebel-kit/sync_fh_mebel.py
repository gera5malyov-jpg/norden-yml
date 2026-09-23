#!/usr/bin/env python3
import argparse
import hashlib
import json
import mimetypes
import os
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

BASE_URL = "https://fh-mebel.ru"
SITEMAP_URL = BASE_URL + "/sitemap.xml"
KIT_API = "https://api.kit.yandex.net"
SUPPLIER = "ТД Никитин"
ROOT_CATEGORY = "ТД Никитин"
WAREHOUSE_NAME = "СПБ"
STOCK_QTY = 100
SKU_PREFIX = "FH-"
REPORT_PATH = "fh-mebel-kit/last_sync_report.json"
UA = "Mozilla/5.0 (compatible; MegapolisCatalogBot/1.0; +https://profikompany.ru)"

PRICE_ATTR_RE = re.compile(r"^data-param(\d+)$")
MONEY_RE = re.compile(r"(\d[\d\s,.]*)\s*(?:руб\.?|₽)", re.I)


def s(v):
    return str(v or "").strip()


def norm(v):
    return " ".join(s(v).casefold().replace("ё", "е").split())


def clean_text(v):
    return " ".join(s(v).replace("\xa0", " ").split())


def as_decimal(v):
    try:
        return Decimal(s(v).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def money_from_text(text):
    m = MONEY_RE.search(clean_text(text))
    if not m:
        return None
    raw = m.group(1).replace(" ", "").replace(",", ".")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def decimal_str(v):
    if v is None:
        return ""
    return f"{v.quantize(Decimal('0.01')):.2f}"


def fetch(session, url, timeout=90):
    last = None
    for attempt in range(5):
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code == 429:
                time.sleep(2 + attempt * 2)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            last = exc
            time.sleep(1 + attempt)
    raise RuntimeError(f"Не удалось скачать {url}: {last}")


def discover_urls(session):
    root = fetch(session, SITEMAP_URL).text
    urls = re.findall(r"https?://fh-mebel\.ru/[^<\s\"']+", root)
    sitemap_parts = [u.rstrip("/") + "/" for u in urls if "/map/sitemap/" in u]
    if not sitemap_parts:
        sitemap_parts = [
            BASE_URL + "/map/sitemap/1/",
            BASE_URL + "/map/sitemap/2/",
        ]

    found = set()
    for part in sitemap_parts:
        body = fetch(session, part, timeout=120).text
        for u in re.findall(r"https?://fh-mebel\.ru/shop/[A-Za-z0-9_%/?=&.+~:#\-]+", body):
            u = u.split("#", 1)[0].split("?", 1)[0]
            if u.rstrip("/") == BASE_URL + "/shop":
                continue
            found.add(u.rstrip("/") + "/")
    return sorted(found), sitemap_parts


def attr_param_constraints(tag):
    out = {}
    if not isinstance(tag, Tag):
        return out
    for key, value in tag.attrs.items():
        m = PRICE_ATTR_RE.match(str(key))
        if m:
            out[m.group(1)] = s(value)
    return out


def combo_text(node, combo):
    parts = []

    def walk(x):
        if isinstance(x, NavigableString):
            t = clean_text(x)
            if t:
                parts.append(t)
            return
        if not isinstance(x, Tag):
            return
        constraints = attr_param_constraints(x)
        for pid, value in constraints.items():
            if pid in combo and combo[pid] != value:
                return
        if x.name in {"script", "style", "input", "svg", "use"}:
            return
        for child in x.children:
            walk(child)

    walk(node)
    return clean_text(" ".join(parts))


def label_for_param_row(row, param_id):
    if not row:
        return f"Параметр {param_id}"
    for sel in [
        ".param_rowLabel", ".param_label", ".product_paramLabel",
        ".product_paramName", ".param_name", "label"
    ]:
        node = row.select_one(sel)
        if node:
            text = clean_text(node.get_text(" ", strip=True)).rstrip(":")
            if text:
                return text
    text = clean_text(row.get_text(" ", strip=True))
    hidden = row.find("input", attrs={"name": f"param{param_id}"})
    if hidden and hidden.parent:
        values = clean_text(hidden.parent.get_text(" ", strip=True))
        if values and text.endswith(values):
            text = clean_text(text[:-len(values)])
    text = text.rstrip(": ")
    if text and len(text) < 100:
        return text
    return f"Параметр {param_id}"


def parse_param_defs(soup):
    defs = {}
    seen = set()
    for hidden in soup.select('input.js_affect_param_value[name^="param"]'):
        name = s(hidden.get("name"))
        m = re.fullmatch(r"param(\d+)", name)
        if not m:
            continue
        pid = m.group(1)
        if pid in seen:
            continue
        seen.add(pid)

        row = hidden.find_parent(class_=lambda x: x and "param_row" in str(x).split())
        if row is None:
            row = hidden.parent
            for _ in range(3):
                if row and row.parent and (
                    row.parent.select_one(f'input[name="param{pid}"]') is not None
                ):
                    candidate = row.parent
                    if len(candidate.select(f'input[name="param{pid}"]')) == 1:
                        row = candidate
                else:
                    break
        title = label_for_param_row(row, pid)

        options = {}
        scope = row or hidden.parent
        if scope:
            for opt in scope.select(".js_affect_param[data-value]"):
                value = s(opt.get("data-value"))
                if not value:
                    continue
                label = clean_text(opt.get("alt") or opt.get("title") or opt.get_text(" ", strip=True))
                if not label and opt.get("data-link-quality"):
                    label = clean_text(opt.get("title"))
                if label:
                    options[value] = label

        current = s(hidden.get("value"))
        defs[pid] = {
            "title": title,
            "current": current,
            "options": options,
        }
    return defs


def parse_good_id(soup):
    node = soup.select_one('input[name="good_id"]')
    return s(node.get("value")) if node else ""


def product_breadcrumbs(soup):
    crumbs = []
    for a in soup.select('[itemtype*="BreadcrumbList"] a[itemprop="item"], a[itemprop="item"]'):
        href = urljoin(BASE_URL, s(a.get("href")))
        text = clean_text(a.get_text(" ", strip=True))
        if not text or href.rstrip("/") in {BASE_URL.rstrip("/"), BASE_URL + "/shop"}:
            continue
        if href.rstrip("/") == ".":
            continue
        if href.rstrip("/") == urljoin(BASE_URL, ".").rstrip("/"):
            continue
        if "/shop/" not in href:
            continue
        crumbs.append(text)
    if crumbs:
        # Last crumb is often the current product.
        h1 = soup.find("h1")
        h1t = clean_text(h1.get_text(" ", strip=True)) if h1 else ""
        if crumbs and norm(crumbs[-1]) == norm(h1t):
            crumbs.pop()
    return crumbs[-3:]


def find_main_param_container(soup):
    prefix = soup.select_one('input[name="prefix"][value="product"]')
    if not prefix:
        return None
    node = prefix.parent
    for _ in range(5):
        if not node or not node.parent:
            break
        if len(node.parent.select('input[name="prefix"][value="product"]')) == 1:
            node = node.parent
        else:
            break
    return node


def parse_features(soup, combo):
    result = {}
    container = find_main_param_container(soup)
    if not container:
        return result

    rows = container.select(".param_row")
    for row in rows:
        label_node = None
        for sel in [".param_rowLabel", ".param_label", ".product_paramLabel", ".product_paramName", ".param_name"]:
            label_node = row.select_one(sel)
            if label_node:
                break
        if not label_node:
            continue
        title = clean_text(label_node.get_text(" ", strip=True)).rstrip(":")
        if not title:
            continue
        value_node = label_node.find_next_sibling()
        value = combo_text(value_node or row, combo)
        if value:
            # Remove the label itself if row fallback was used.
            if norm(value).startswith(norm(title)):
                value = clean_text(value[len(title):]).lstrip(": ")
            if value and norm(value) not in {"увеличить"}:
                result[title] = value

    # Fallback for sites where rows do not expose expected classes.
    if not result:
        text = clean_text(container.get_text("\n", strip=True))
        labels = [
            "Производитель", "Гарантия", "Коллекция", "ШхВхГ (мм)", "Цвет",
            "Материал корпус", "Материал фасад", "Тип основания", "Изголовье",
            "Кол-во дверей", "Внутреннее наполнение", "Компл-я с зеркалом",
            "Спальное место", "Конфигурация"
        ]
        for label in labels:
            m = re.search(re.escape(label) + r"\s*:\s*(.+?)(?=\s(?:"
                          + "|".join(re.escape(x) for x in labels) + r")\s*:|$)", text, re.I)
            if m:
                value = clean_text(m.group(1))
                if value:
                    result[label] = value
    return result


def parse_description(soup):
    selectors = [
        ".product_descriptionText", ".product_description", ".product_text",
        ".shop_product_description", ".product_infoDescription", "[itemprop='description']"
    ]
    best = ""
    for sel in selectors:
        for node in soup.select(sel):
            text = clean_text(node.get_text("\n", strip=True))
            if len(text) > len(best):
                best = text
    if not best:
        # Use the main textual block after the "Описание товара" marker.
        marker = soup.find(string=re.compile(r"Описание товара", re.I))
        if marker:
            parent = marker.parent
            if parent:
                candidate = parent.find_next()
                if candidate:
                    best = clean_text(candidate.get_text("\n", strip=True))
    if not best:
        meta = soup.select_one('meta[name="description"]')
        best = s(meta.get("content")) if meta else ""
    return best[:12000]


def parse_composition(soup):
    rows = []
    for tr in soup.select(".product_mainComposition_table tr"):
        text = clean_text(tr.get_text(" ", strip=True))
        if text:
            rows.append(text)
    return " | ".join(rows)[:8000]


def parse_images(soup):
    images = []
    seen = set()
    for a in soup.select("a.js_product_img[href]"):
        url = urljoin(BASE_URL, s(a.get("href")))
        iid = s(a.get("data-image-id"))
        key = (url, iid)
        if url and key not in seen:
            seen.add(key)
            images.append({"url": url, "image_id": iid})
    # Fallback: gallery links may only be identified by /h_quality/.
    if not images:
        for a in soup.select('a[href*="/userfiles/shop/h_quality/"]'):
            if a.find_parent(class_=lambda x: x and (
                "defaultProduct_item" in str(x).split() or
                "collectionProduct_item" in str(x).split()
            )):
                continue
            url = urljoin(BASE_URL, s(a.get("href")))
            iid = s(a.get("data-image-id"))
            key = (url, iid)
            if url and key not in seen:
                seen.add(key)
                images.append({"url": url, "image_id": iid})
    return images


def parse_main_price(soup):
    meta = soup.select_one('.product_priceWrap meta[itemprop="price"]')
    if meta:
        d = as_decimal(meta.get("content"))
        if d and d > 0:
            return d
    wrap = soup.select_one(".product_priceWrap")
    if wrap:
        d = money_from_text(wrap.get_text(" ", strip=True))
        if d and d > 0:
            return d
    return None


def parse_variant_prices(soup):
    wrap = soup.select_one(".product_priceWrap")
    if not wrap:
        return []
    rows = []
    for node in wrap.select(".js_priceMain[data-price-val]"):
        combo = {}
        for key, val in node.attrs.items():
            m = PRICE_ATTR_RE.match(str(key))
            if m:
                combo[m.group(1)] = s(val)
        if not combo:
            continue
        price = as_decimal(node.get("data-price-val"))
        if price is None or price <= 0:
            continue
        rows.append({
            "combo": combo,
            "price": price,
            "price_id": s(node.get("data-price-id")),
            "image_id": s(node.get("data-image-id")),
        })
    return rows


def variant_title(param_defs, combo):
    chunks = []
    for pid in sorted(combo, key=lambda x: int(x) if x.isdigit() else x):
        info = param_defs.get(pid, {})
        title = clean_text(info.get("title")) or f"Параметр {pid}"
        label = clean_text((info.get("options") or {}).get(combo[pid])) or combo[pid]
        chunks.append(f"{title}: {label}")
    return "; ".join(chunks)


def make_sku(good_id, combo):
    base = f"{SKU_PREFIX}{good_id}"
    if not combo:
        return base
    tail = "-".join(
        f"{pid}-{combo[pid]}"
        for pid in sorted(combo, key=lambda x: int(x) if x.isdigit() else x)
    )
    sku = f"{base}-{tail}"
    if len(sku) <= 80:
        return sku
    digest = hashlib.sha1(tail.encode("utf-8")).hexdigest()[:12].upper()
    return f"{base}-{digest}"


def parse_article(features, good_id):
    for k, v in features.items():
        if norm(k) in {"артикул", "артикул товара", "код", "код товара"} and clean_text(v):
            return clean_text(v)
    return good_id


def brand_from_features(features):
    for k, v in features.items():
        if norm(k) in {"производитель", "бренд"}:
            brand = clean_text(v)
            brand = re.sub(r"^[«\"]|[»\"]$", "", brand)
            brand = brand.split(",", 1)[0].strip(" «»\"")
            if brand:
                return brand[:200]
    return SUPPLIER


def choose_images(images, image_id):
    if not image_id:
        return [x["url"] for x in images]
    first = [x["url"] for x in images if x.get("image_id") == image_id]
    rest = [x["url"] for x in images if x.get("image_id") != image_id]
    return first + rest


def parse_product(session, url):
    r = fetch(session, url, timeout=75)
    soup = BeautifulSoup(r.text, "lxml")
    h1 = soup.find("h1")
    good_id = parse_good_id(soup)
    wrap = soup.select_one(".product_priceWrap")
    if not h1 or not good_id or not wrap:
        return None

    name = clean_text(h1.get_text(" ", strip=True))
    if not name:
        return None

    param_defs = parse_param_defs(soup)
    variant_prices = parse_variant_prices(soup)
    base_price = parse_main_price(soup)
    images = parse_images(soup)
    description = parse_description(soup)
    composition = parse_composition(soup)
    breadcrumbs = product_breadcrumbs(soup)

    combos = variant_prices or [{
        "combo": {},
        "price": base_price,
        "price_id": "",
        "image_id": "",
    }]

    offers = []
    for row in combos:
        if row["price"] is None or row["price"] <= 0:
            continue
        combo = dict(row["combo"])
        features = parse_features(soup, combo)
        modification = variant_title(param_defs, combo)
        title = name + (f" — {modification}" if modification else "")
        article = parse_article(features, good_id)
        offers.append({
            "source_url": url,
            "good_id": good_id,
            "price_id": row["price_id"],
            "sku": make_sku(good_id, combo),
            "supplier_article": article,
            "name": title[:500],
            "base_name": name,
            "price": row["price"],
            "brand": brand_from_features(features),
            "description": description,
            "features": features,
            "modification": modification,
            "combo": combo,
            "pictures": choose_images(images, row["image_id"]),
            "category_path": breadcrumbs,
            "composition": composition,
        })
    if not offers:
        return None
    return {
        "url": url,
        "good_id": good_id,
        "offers": offers,
    }


class KitClient:
    def __init__(self, token):
        self.session = requests.Session()
        self.token = token
        self.last_request_at = 0.0
        self.image_cache = {}

    def request(self, method, path, *, params=None, body=None, files=None, timeout=120):
        url = path if path.startswith("http") else KIT_API + path
        for attempt in range(15):
            delay = 0.60 - (time.monotonic() - self.last_request_at)
            if delay > 0:
                time.sleep(delay)
            self.last_request_at = time.monotonic()
            headers = {
                "Authorization": "Bearer " + self.token,
                "Accept": "application/json",
            }
            if method == "PATCH":
                headers["Content-Type"] = "application/merge-patch+json"
            try:
                resp = self.session.request(
                    method, url, params=params, json=body, files=files,
                    headers=headers, timeout=timeout
                )
            except requests.RequestException:
                if attempt == 14:
                    raise
                time.sleep(min(15, attempt + 1))
                continue
            if resp.status_code == 429:
                time.sleep(float(resp.headers.get("Retry-After") or min(20, 2 + attempt)))
                continue
            if resp.status_code >= 500:
                if attempt == 14:
                    resp.raise_for_status()
                time.sleep(min(15, attempt + 1))
                continue
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"KIT {method} {path}: HTTP {resp.status_code}: {resp.text[:1200]}"
                )
            return resp.json() if resp.content else {}
        raise RuntimeError("KIT request retries exhausted")

    def list_all(self, path, params=None, preferred_key=None):
        rows = []
        page = 1
        while True:
            q = dict(params or {})
            q.update({"page": page, "per_page": 100})
            payload = self.request("GET", path, params=q)
            batch = []
            if preferred_key and isinstance(payload.get(preferred_key), list):
                batch = payload[preferred_key]
            else:
                for v in payload.values():
                    if isinstance(v, list):
                        batch = v
                        break
            rows.extend(x for x in batch if isinstance(x, dict))
            total = payload.get("total_count") or payload.get("total")
            if not batch or len(batch) < 100 or (total is not None and len(rows) >= int(total)):
                break
            page += 1
        return rows

    def create_category(self, title, parent_id=None):
        body = {"title": title}
        if parent_id:
            body["parent_id"] = parent_id
        return self.request("POST", "/v1/categories", body=body)

    def create_characteristic(self, title):
        return self.request(
            "POST", "/v1/characteristics",
            body={"title": title, "type": "STRING", "select_mode": "SINGLE"},
        )

    def create_product(self, category_id):
        return self.request("POST", "/v1/products", body={"category_ids": [category_id]})

    def create_variant(self, body):
        return self.request("POST", "/v1/variants", body=body)

    def get_variant(self, variant_id):
        return self.request("GET", f"/v1/variants/{variant_id}")

    def patch_variant(self, variant_id, body):
        return self.request("PATCH", f"/v1/variants/{variant_id}", body=body)

    def archive_variant(self, variant_id):
        return self.request("POST", f"/v1/variants/{variant_id}/archive")

    def update_prices(self, items):
        if items:
            self.request("POST", "/v1/variants/prices/bulk_update", body={"items": items})

    def update_stocks(self, items):
        if items:
            self.request("POST", "/v1/variants/stocks/bulk_update", body={"items": items})

    def upload_image_url(self, url):
        if url in self.image_cache:
            return self.image_cache[url]
        r = requests.get(url, timeout=120, headers={"User-Agent": UA})
        r.raise_for_status()
        ctype = r.headers.get("Content-Type") or mimetypes.guess_type(urlparse(url).path)[0] or "image/jpeg"
        ext = os.path.splitext(urlparse(url).path)[1] or ".jpg"
        payload = self.request(
            "POST", "/v1/files",
            files={"file": ("fh-mebel" + ext[:10], r.content, ctype)},
            timeout=150,
        )
        file_id = s(payload.get("id"))
        if file_id:
            self.image_cache[url] = file_id
        return file_id


def crawl_catalog(urls, workers=6, max_pages=0):
    if max_pages:
        urls = urls[:max_pages]
    products = []
    errors = []
    local = {}

    def worker(url):
        # One session per worker thread via thread id-like cache is unnecessary; create small session per task.
        sess = requests.Session()
        sess.headers.update({"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"})
        return parse_product(sess, url)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(worker, url): url for url in urls}
        done = 0
        for fut in as_completed(futures):
            url = futures[fut]
            done += 1
            try:
                item = fut.result()
                if item:
                    products.append(item)
            except Exception as exc:
                errors.append({"url": url, "message": str(exc)[:1000]})
            if done % 100 == 0:
                print(f"Проверено страниц: {done}/{len(urls)}; товаров: {len(products)}; ошибок: {len(errors)}")
    products.sort(key=lambda x: x["url"])
    return products, errors


def build_report(urls, sitemap_parts, products, crawl_errors):
    offers = [o for p in products for o in p["offers"]]
    return {
        "status": "parsed",
        "complete": False,
        "source": BASE_URL,
        "supplier": SUPPLIER,
        "sitemap_parts": sitemap_parts,
        "sitemap_shop_urls": len(urls),
        "product_pages": len(products),
        "offers_after_modifications": len(offers),
        "modification_cards": sum(1 for o in offers if o["modification"]),
        "warehouse_rule": f"{WAREHOUSE_NAME} = {STOCK_QTY}",
        "price_rule": "цена для покупателя = текущая цена fh-mebel.ru",
        "sku_rule": "FH-<good_id>[-<param>-<value>...]",
        "crawl_errors": crawl_errors[:300],
        "processed": 0,
        "created": 0,
        "updated_existing": 0,
        "duplicates_archived": 0,
        "images_uploaded": 0,
        "image_errors": 0,
        "price_updates": 0,
        "stock_updates": 0,
        "errors": [],
        "warnings": [],
        "sample": [
            {
                "sku": o["sku"],
                "name": o["name"],
                "price": decimal_str(o["price"]),
                "brand": o["brand"],
                "modification": o["modification"],
                "pictures": len(o["pictures"]),
                "source_url": o["source_url"],
            }
            for o in offers[:20]
        ],
    }


def save_report(report):
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def run(dry_run=False, max_pages=0, workers=6):
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"})
    urls, sitemap_parts = discover_urls(sess)
    products, crawl_errors = crawl_catalog(urls, workers=workers, max_pages=max_pages)
    report = build_report(urls[:max_pages] if max_pages else urls, sitemap_parts, products, crawl_errors)
    offers = [o for p in products for o in p["offers"]]

    if not offers:
        report["status"] = "error"
        report["errors"].append({"message": "Не найдено ни одной товарной карточки с ценой"})
        save_report(report)
        return 1

    if dry_run:
        report["status"] = "dry_run_ok"
        report["complete"] = True
        save_report(report)
        return 0

    token = s(os.environ.get("YANDEX_KIT_TOKEN"))
    if not token:
        report["status"] = "error"
        report["errors"].append({"message": "YANDEX_KIT_TOKEN не настроен"})
        save_report(report)
        return 2

    kit = KitClient(token)

    warehouses = kit.list_all("/v1/warehouses", {"status": "ACTIVE"}, "warehouses")
    warehouse_id = ""
    for row in warehouses:
        if norm(row.get("title")) == norm(WAREHOUSE_NAME):
            warehouse_id = s(row.get("id"))
            break
    if not warehouse_id:
        report["status"] = "error"
        report["errors"].append({"message": f"В KIT не найден активный склад {WAREHOUSE_NAME!r}"})
        save_report(report)
        return 3

    categories = kit.list_all("/v1/categories", {"status": "ACTIVE"}, "categories")

    def ensure_category(title, parent_id=""):
        for row in categories:
            if norm(row.get("title")) == norm(title) and s(row.get("parent_id")) == s(parent_id):
                return s(row.get("id"))
        created = kit.create_category(title, parent_id or None)
        cid = s(created.get("id"))
        if not cid:
            raise RuntimeError(f"KIT не вернул id категории {title!r}")
        copy = dict(created)
        copy.setdefault("parent_id", parent_id)
        categories.append(copy)
        return cid

    root_id = ensure_category(ROOT_CATEGORY)
    category_cache = {(): root_id}

    def category_for(path):
        parent = root_id
        acc = []
        for title in path:
            title = clean_text(title)
            if not title or norm(title) == norm(ROOT_CATEGORY):
                continue
            acc.append(title)
            key = tuple(acc)
            if key not in category_cache:
                category_cache[key] = ensure_category(title[:250], parent)
            parent = category_cache[key]
        return parent

    characteristics = kit.list_all("/v1/characteristics", {"status": "ACTIVE"}, "characteristics")
    char_cache = {}

    def ensure_characteristic(title):
        key = norm(title)
        if key in char_cache:
            return char_cache[key]
        matches = [x for x in characteristics if norm(x.get("title")) == key]
        if matches:
            compatible = [x for x in matches if s(x.get("type")).upper() in {"STRING", "MULTIPLE_STRING"}]
            chosen = (compatible or matches)[0]
            cid = s(chosen.get("id"))
            char_cache[key] = cid
            return cid
        created = kit.create_characteristic(title[:250])
        cid = s(created.get("id"))
        if not cid:
            raise RuntimeError(f"KIT не вернул id характеристики {title!r}")
        characteristics.append(created)
        char_cache[key] = cid
        return cid

    def characteristic_payload(item):
        pairs = []

        def add(title, value):
            title = clean_text(title)
            value = clean_text(value)
            if not title or not value:
                return
            if norm(title) in {"остаток", "наличие", "количество", "кол-во"}:
                return
            for i, (t, _) in enumerate(pairs):
                if norm(t) == norm(title):
                    pairs[i] = (title, value)
                    return
            pairs.append((title, value))

        add("Поставщик", SUPPLIER)
        add("Артикул", item["sku"])
        add("Код для сайта", item["sku"])
        add("Артикул поставщика", item["supplier_article"])
        add("ID FH-Mebel", item["good_id"])
        add("ID цены FH-Mebel", item["price_id"])
        add("Ссылка поставщика", item["source_url"])
        add("Модификация", item["modification"])
        add("Состав комплекта", item["composition"])
        for title, value in item["features"].items():
            add(title, value)

        out = []
        for title, value in pairs:
            try:
                cid = ensure_characteristic(title)
                out.append({
                    "characteristic_id": cid,
                    "value": value[:4000],
                    "values": [value[:4000]],
                })
            except Exception as exc:
                report["warnings"].append(f"{item['sku']}: характеристика {title}: {exc}")
        return out

    def find_exact(sku):
        payload = kit.request(
            "GET", "/v1/variants",
            params={"name": sku, "page": 1, "per_page": 100},
        )
        rows = payload.get("variants") or payload.get("items") or payload.get("results") or []
        if isinstance(rows, dict):
            rows = rows.get("items") or []
        return [
            x for x in rows
            if s(x.get("sku")) == sku and s(x.get("status")).upper() != "ARCHIVED"
        ]

    def media_payload(item):
        media = []
        for url in item["pictures"]:
            try:
                file_id = kit.upload_image_url(url)
                if file_id:
                    media.append({
                        "type": "IMAGE",
                        "display_sequence": len(media),
                        "image_id": file_id,
                    })
                    report["images_uploaded"] += 1
            except Exception as exc:
                report["image_errors"] += 1
                if len(report["warnings"]) < 500:
                    report["warnings"].append(f"{item['sku']}: изображение {url}: {exc}")
        return media

    price_batch = []
    stock_batch = []

    for index, item in enumerate(offers, 1):
        try:
            exact = find_exact(item["sku"])
            if len(exact) > 1:
                exact = sorted(
                    exact,
                    key=lambda x: (
                        int(x.get("kit_id") or 10**18),
                        s(x.get("created_at")),
                        s(x.get("id")),
                    ),
                )
                keeper = exact[0]
                for dup in exact[1:]:
                    did = s(dup.get("id"))
                    if did:
                        kit.archive_variant(did)
                        report["duplicates_archived"] += 1
                exact = [keeper]

            category_id = category_for(item["category_path"])
            chars = characteristic_payload(item)
            price = decimal_str(item["price"])
            stocks = [{"warehouse_id": warehouse_id, "quantity": STOCK_QTY, "reserved": 0}]

            if exact:
                current = kit.get_variant(s(exact[0].get("id")))
                vid = s(current.get("id"))
                patch = {
                    "name": item["name"],
                    "description": item["description"],
                    "brand": item["brand"],
                    "characteristics": chars,
                }
                # Keep existing media to avoid creating duplicate KIT files every 14 days.
                if not (current.get("media") or []) and item["pictures"]:
                    media = media_payload(item)
                    if media:
                        patch["media"] = media
                kit.patch_variant(vid, patch)
                report["updated_existing"] += 1
            else:
                product = kit.create_product(category_id)
                product_id = s(product.get("id"))
                if not product_id:
                    raise RuntimeError("KIT не вернул product_id")
                body = {
                    "sku": item["sku"],
                    "name": item["name"],
                    "description": item["description"],
                    "status": "PUBLISHED",
                    "product_id": product_id,
                    "brand": item["brand"],
                    "stocks": stocks,
                    "pricing": {"price": price, "manual_discount_price": price},
                    "characteristics": chars,
                }
                if item["pictures"]:
                    media = media_payload(item)
                    if media:
                        body["media"] = media
                created = kit.create_variant(body)
                vid = s(created.get("id"))
                if not vid:
                    raise RuntimeError("KIT не вернул variant_id")
                report["created"] += 1

            price_batch.append({
                "variant_id": vid,
                "price": price,
                "manual_discount_price": price,
            })
            stock_batch.append({
                "variant_id": vid,
                "warehouse_id": warehouse_id,
                "quantity": STOCK_QTY,
            })
            report["price_updates"] += 1
            report["stock_updates"] += 1
            report["processed"] += 1

            if len(price_batch) >= 100:
                kit.update_prices(price_batch)
                price_batch.clear()
            if len(stock_batch) >= 200:
                kit.update_stocks(stock_batch)
                stock_batch.clear()
            if index % 50 == 0:
                print(f"KIT: обработано {index}/{len(offers)}")

        except Exception as exc:
            report["errors"].append({
                "sku": item["sku"],
                "source_url": item["source_url"],
                "message": str(exc)[:1500],
            })

    if price_batch:
        kit.update_prices(price_batch)
    if stock_batch:
        kit.update_stocks(stock_batch)

    report["status"] = "ok" if not report["errors"] else "degraded"
    report["complete"] = report["processed"] == len(offers) and not report["errors"]
    save_report(report)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-pages", type=int, default=0)
    p.add_argument("--workers", type=int, default=6)
    args = p.parse_args()
    raise SystemExit(run(
        dry_run=args.dry_run,
        max_pages=max(0, args.max_pages),
        workers=max(1, min(args.workers, 12)),
    ))
