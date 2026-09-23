#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import time
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://fh-mebel.ru/"
KIT_API = "https://api.kit.yandex.net"
ROOT_CATEGORY = "FH мебель"
SUPPLIER = "ТД Никитин"
WAREHOUSE_NAME = "СПБ"
STOCK_QTY = 100
SKU_PREFIX = "FH-"
HERE = Path(__file__).resolve().parent
REPORT_PATH = HERE / "last_sync_report.json"
STATE_PATH = HERE / "state.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/153 Safari/537.36 FH-Mebel-KIT-Sync"
LIVE_INTERVAL_SECONDS = 14 * 24 * 60 * 60

EXCLUDED_PATH_BITS = (
    "/shop/cart/",
    "/shop/search/",
    "/login/",
    "/registration/",
)
ASSET_EXTS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".pdf", ".zip", ".rar", ".css", ".js", ".xml", ".xlsx", ".xls",
)


def s(value):
    return str(value or "").strip()


def norm(value):
    return " ".join(s(value).casefold().replace("ё", "е").split())


def money(value):
    try:
        raw = s(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
        raw = re.sub(r"[^\d.]", "", raw)
        if not raw:
            return None
        d = Decimal(raw)
        return d if d > 0 else None
    except (InvalidOperation, ValueError):
        return None


def clean_text(value):
    return " ".join(s(value).replace("\xa0", " ").split())


def unique(values):
    out = []
    seen = set()
    for value in values:
        value = s(value)
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat(timespec="seconds")


def load_state():
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def due_for_live(force=False):
    if force:
        return True, None
    state = load_state()
    stamp = s(state.get("last_success_at"))
    if not stamp:
        return True, None
    try:
        then = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        age = (utc_now() - then.astimezone(timezone.utc)).total_seconds()
        return age >= LIVE_INTERVAL_SECONDS, age
    except Exception:
        return True, None


def canonical_url(href, base=BASE_URL):
    if not href:
        return ""
    url = urljoin(base, href)
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return ""
    if p.netloc.casefold() not in ("fh-mebel.ru", "www.fh-mebel.ru"):
        return ""
    path = re.sub(r"/{2,}", "/", p.path or "/")
    if any(path.casefold().endswith(ext) for ext in ASSET_EXTS):
        return ""
    if any(bit in path.casefold() for bit in EXCLUDED_PATH_BITS):
        return ""
    if path != "/" and not path.endswith("/"):
        path += "/"
    return urlunparse(("https", "fh-mebel.ru", path, "", "", ""))


def is_catalog_url(url):
    path = urlparse(url).path
    return path == "/" or path.startswith("/shop/")


def session():
    ses = requests.Session()
    ses.headers.update({
        "User-Agent": UA,
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
    })
    return ses


def fetch_url(url, timeout=60):
    ses = session()
    last = None
    for attempt in range(4):
        try:
            r = ses.get(url, timeout=timeout, allow_redirects=True)
            if r.status_code == 429:
                time.sleep(min(10, 2 + attempt * 2))
                continue
            if r.status_code >= 500:
                time.sleep(min(8, 1 + attempt))
                continue
            return url, r.status_code, r.text, r.url
        except Exception as exc:
            last = exc
            time.sleep(min(5, 1 + attempt))
    return url, 0, "", str(last or "fetch failed")


def sitemap_urls():
    ses = session()
    candidates = [
        urljoin(BASE_URL, "sitemap.xml"),
        urljoin(BASE_URL, "sitemap_index.xml"),
        urljoin(BASE_URL, "map/sitemap/1/"),
        urljoin(BASE_URL, "map/sitemap/2/"),
    ]
    try:
        r = ses.get(urljoin(BASE_URL, "robots.txt"), timeout=30)
        if r.ok:
            for line in r.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    candidates.append(line.split(":", 1)[1].strip())
    except Exception:
        pass

    found = set()
    checked = set()
    queue = deque(unique(candidates))
    while queue and len(checked) < 40:
        url = queue.popleft()
        if url in checked:
            continue
        checked.add(url)
        try:
            r = ses.get(url, timeout=60)
            if not r.ok:
                continue
            text = r.text

            discovered = set(re.findall(
                r"https?://(?:www\\.)?fh-mebel\\.ru/[^<\\s\"']+",
                text,
                re.I,
            ))

            try:
                root = ET.fromstring(r.content)
                for node in root.iter():
                    if node.tag.split("}")[-1].casefold() == "loc" and clean_text(node.text):
                        discovered.add(clean_text(node.text))
            except Exception:
                pass

            for loc in discovered:
                clean = loc.split("#", 1)[0]
                low = clean.casefold()
                if "/map/sitemap/" in low or low.endswith(".xml") or low.endswith(".xml/"):
                    if clean not in checked:
                        queue.append(clean)
                    continue
                cu = canonical_url(clean)
                if cu and is_catalog_url(cu):
                    found.add(cu)
        except Exception:
            continue
    return found

def closest_group_name(node, main, param_name):
    for ancestor in node.parents:
        if ancestor is main:
            break
        labels = []
        for candidate in ancestor.find_all(
            ["div", "span", "label"],
            class_=re.compile(r"(label|title|name)", re.I),
            recursive=True,
        ):
            tx = clean_text(candidate.get_text(" ", strip=True))
            if 1 <= len(tx) <= 80 and (tx.endswith(":") or "место" in tx.casefold() or "конфигура" in tx.casefold()):
                labels.append(tx.rstrip(":"))
        if labels:
            return labels[0]
    fallback = {
        "param37": "Спальное место",
        "param40": "Цвет",
    }
    return fallback.get(param_name, param_name)


def option_maps(main):
    by_param = {}
    label_by_param = {}

    for opt in main.select(".js_affect_param[data-value]"):
        value = s(opt.get("data-value"))
        if not value:
            continue

        param_name = ""
        group_root = None
        for ancestor in opt.parents:
            if ancestor is main:
                break
            inputs = ancestor.select("input.js_affect_param_value[name^='param']")
            names = unique([s(x.get("name")) for x in inputs if s(x.get("name"))])
            if len(names) == 1:
                param_name = names[0]
                group_root = ancestor
                break
        if not param_name:
            continue

        label = (
            clean_text(opt.get("title"))
            or clean_text(opt.get("data-title"))
            or clean_text(opt.get_text(" ", strip=True))
        )
        if not label:
            named = main.select_one(f".colorImgs_name[data-value='{value}']")
            if named:
                label = clean_text(named.get_text(" ", strip=True))
        if not label:
            label = value

        group_name = closest_group_name(opt, main, param_name)
        label_by_param.setdefault(param_name, group_name)

        if "спаль" in norm(group_name) or "спальное место" in norm(group_name):
            parent_text = clean_text((group_root or opt.parent).get_text(" ", strip=True))
            if re.fullmatch(r"\d{3,4}", label) and re.search(r"[xх×]\s*\d{3,4}", parent_text, re.I):
                tail = re.search(r"[xх×]\s*(\d{3,4})", parent_text, re.I)
                if tail:
                    label = f"{label} x {tail.group(1)}"

        by_param.setdefault(param_name, {})[value] = label

    for hidden in main.select("input.js_affect_param_value[name^='param']"):
        name = s(hidden.get("name"))
        value = s(hidden.get("value"))
        if not name or not value:
            continue
        label_by_param.setdefault(name, closest_group_name(hidden, main, name))
        by_param.setdefault(name, {})
        if value not in by_param[name]:
            by_param[name][value] = value

    return by_param, label_by_param


def parse_pairs(main):
    pairs = {}

    def add(label, value):
        label = clean_text(label).rstrip(":")
        value = clean_text(value)
        if not label or not value:
            return
        if len(label) > 100 or len(value) > 2000:
            return
        pairs.setdefault(label, value)

    for row in main.select(".param_row"):
        lab = row.select_one(".param_rowLabel")
        val = row.select_one(".param_rowValue")
        if lab and val:
            add(lab.get_text(" ", strip=True), val.get_text(" ", strip=True))

    # FH also uses a few labelled rows outside .param_row.
    for lab in main.find_all(class_=re.compile(r"(param|product).*label", re.I)):
        label = clean_text(lab.get_text(" ", strip=True))
        if not label or len(label) > 100:
            continue
        parent = lab.parent
        if not parent:
            continue
        candidates = [
            x for x in parent.find_all(
                class_=re.compile(r"(value|text)", re.I),
                recursive=False,
            )
            if x is not lab
        ]
        if not candidates:
            siblings = [x for x in lab.next_siblings if getattr(x, "get_text", None)]
            candidates = siblings[:2]
        for val in candidates:
            value = clean_text(val.get_text(" ", strip=True))
            if value and value != label:
                add(label, value)
                break

    # Explicit fields are stable enough to recover from page text if their row uses a special class.
    whole = clean_text(main.get_text(" ", strip=True))
    anchors = [
        ("Производитель", "Гарантия"),
        ("Гарантия", "Коллекция"),
        ("Коллекция", "ШхВхГ"),
    ]
    for label, nxt in anchors:
        if label in pairs:
            continue
        m = re.search(
            rf"{re.escape(label)}:\s*(.+?)\s*(?={re.escape(nxt)}:)",
            whole,
            re.I,
        )
        if m:
            add(label, m.group(1))
    return pairs


def parse_description(soup, main):
    selectors = [
        "[itemprop='description']",
        ".product_descriptionText",
        ".product_textDescription",
        ".product_description",
        ".product_aboutText",
        ".product_text",
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            tx = clean_text(node.get_text(" ", strip=True))
            if len(tx) >= 80 and "Доставка:" not in tx[:80]:
                return tx

    heading = soup.find(
        lambda tag: getattr(tag, "get_text", None)
        and clean_text(tag.get_text(" ", strip=True)).casefold() == "описание товара"
    )
    if heading:
        collected = []
        node = heading
        for _ in range(12):
            node = node.find_next()
            if not node:
                break
            cls = " ".join(node.get("class", [])) if hasattr(node, "get") else ""
            if "recommendation" in cls.casefold():
                break
            if getattr(node, "name", "") in ("p", "li"):
                tx = clean_text(node.get_text(" ", strip=True))
                if tx and tx not in collected:
                    collected.append(tx)
        tx = " ".join(collected)
        if len(tx) >= 80:
            return tx

    meta = soup.find("meta", attrs={"name": "description"})
    return clean_text(meta.get("content")) if meta else ""


def product_images(main, url):
    pics = []
    for node in main.select(".product_photo a.js_product_img[href], .product_photoMain a[href], a[itemprop='image'][href]"):
        href = s(node.get("href"))
        if href and "/userfiles/shop/" in href:
            pics.append(urljoin(url, href))
    if not pics:
        for img in main.select(".product_photo img[src], .product_photo img[data-src]"):
            src = s(img.get("data-src") or img.get("src"))
            if src and "/userfiles/shop/" in src:
                pics.append(urljoin(url, src))
    return unique(pics)


def product_composition(soup):
    rows = []
    for tr in soup.select('.product_mainComposition_table tr'):
        tx = clean_text(tr.get_text(' ', strip=True))
        if tx and tx not in rows:
            rows.append(tx)
    return ' | '.join(rows)


def breadcrumb_path(soup):
    names = []
    for node in soup.select(".bread_unit [itemprop='name'], .bread_unit span"):
        tx = clean_text(node.get_text(" ", strip=True))
        if tx and tx not in names:
            names.append(tx)
    if names:
        names = names[:-1]
    return [x for x in names if norm(x) not in {"главная", "каталог", "мебель"}]


def variant_characteristics(base_pairs, mods, option_by_param, label_by_param, main):
    out = dict(base_pairs)

    for param_name, param_value in mods.items():
        label = label_by_param.get(param_name, param_name)
        value = option_by_param.get(param_name, {}).get(param_value, param_value)
        out[label] = value

    # Recover variant-sensitive numeric fields, e.g. width changes with bed size.
    for row in main.select(".param_row"):
        lab = row.select_one(".param_rowLabel")
        val = row.select_one(".param_rowValue")
        if not lab or not val:
            continue
        label = clean_text(lab.get_text(" ", strip=True)).rstrip(":")
        spans = val.select(".product_nummulti")
        if not spans:
            continue
        text = clean_text(val.get_text(" ", strip=True))
        for param_name, param_value in mods.items():
            chosen = val.select_one(f".product_nummulti[data-{param_name}='{param_value}']")
            if chosen:
                all_prefixes = [clean_text(x.get_text(" ", strip=True)) for x in spans]
                selected = clean_text(chosen.get_text(" ", strip=True))
                # The site prints all alternative prefixes followed by the common dimension tail.
                tmp = text
                for prefix in all_prefixes:
                    tmp = re.sub(rf"^\s*{re.escape(prefix)}\s*", "", tmp, count=1)
                out[label] = clean_text(f"{selected} {tmp}")
                break
    return out


def parse_product(url, html):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("div.product_info.js_product_item[data-element-id]")
    if not main:
        main = soup.select_one("[itemtype*='schema.org/Product'].js_product_item[data-element-id]")
    if not main:
        return []

    element_id = s(main.get("data-element-id"))
    h1 = main.find("h1") or soup.find("h1")
    name = clean_text(h1.get_text(" ", strip=True) if h1 else "")
    if not element_id or not name:
        return []

    base_pairs = parse_pairs(main)
    option_by_param, label_by_param = option_maps(main)
    description = parse_description(soup, main)
    pictures = product_images(main, url)
    composition = product_composition(soup)
    categories = breadcrumb_path(soup)

    manufacturer = (
        base_pairs.get("Производитель")
        or base_pairs.get("производитель")
        or ""
    )
    manufacturer = clean_text(manufacturer)

    price_root = main.select_one(".product_priceWrap") or main.select_one(".product_currentPrice") or main
    raw_spans = price_root.select(".js_priceMain[data-price-val]")
    priced = []
    for span in raw_spans:
        value = money(span.get("data-price-val"))
        if value is None:
            continue
        attrs = {
            key[len("data-"):]: s(val)
            for key, val in span.attrs.items()
            if key.startswith("data-param") and s(val)
        }
        price_id = s(span.get("data-price-id"))
        priced.append((span, value, price_id, attrs))

    # Some collection cards have a static displayed total and an internal 100-ruble technical price.
    visible_prices = []
    for node in price_root.select(".shop_param_price"):
        tx = clean_text(node.get_text(" ", strip=True))
        d = money(tx)
        if d is not None:
            visible_prices.append(d)

    meaningful = [x for x in priced if x[1] >= Decimal("500")]
    if meaningful:
        priced = meaningful
    elif visible_prices:
        priced = [(None, visible_prices[0], "", {})]

    if not priced:
        text = clean_text(price_root.get_text(" ", strip=True))
        candidates = [
            money(x)
            for x in re.findall(r"\d[\d\s,.]*\s*(?:руб\.?|₽)", text, re.I)
        ]
        candidates = [x for x in candidates if x is not None]
        if candidates:
            priced = [(None, candidates[0], "", {})]

    # Avoid treating an old/current price pair as modifications if neither has variant params.
    no_param = [x for x in priced if not x[3]]
    with_param = [x for x in priced if x[3]]
    if with_param:
        priced = with_param
    elif len(no_param) > 1:
        # The current price is normally the smallest visible sale price.
        priced = [min(no_param, key=lambda x: x[1])]

    cards = []
    seen_signatures = set()
    for _, price, price_id, mods in priced:
        signature = tuple(sorted(mods.items()))
        dedup_key = (price_id, signature, str(price))
        if dedup_key in seen_signatures:
            continue
        seen_signatures.add(dedup_key)

        chars = variant_characteristics(
            base_pairs, mods, option_by_param, label_by_param, main
        )

        mod_labels = []
        for param_name, param_value in sorted(mods.items()):
            label = label_by_param.get(param_name, param_name)
            val = option_by_param.get(param_name, {}).get(param_value, param_value)
            mod_labels.append(f"{label}: {val}")

        display_name = name
        if mod_labels:
            display_name += " — " + "; ".join(mod_labels)

        if price_id:
            sku = f"{SKU_PREFIX}{element_id}-{price_id}"
        else:
            raw = element_id + "|" + "|".join(f"{k}={v}" for k, v in signature)
            suffix = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8] if signature else ""
            sku = f"{SKU_PREFIX}{element_id}" + (f"-{suffix}" if suffix else "")

        cards.append({
            "sku": sku,
            "source_element_id": element_id,
            "source_price_id": price_id,
            "source_url": url,
            "name": display_name,
            "base_name": name,
            "price": str(price.quantize(Decimal("0.01"))),
            "manufacturer": manufacturer,
            "supplier": SUPPLIER,
            "description": description,
            "pictures": pictures,
            "composition": composition,
            "category_path": categories,
            "characteristics": chars,
            "modifications": {
                label_by_param.get(k, k): option_by_param.get(k, {}).get(v, v)
                for k, v in mods.items()
            },
            "raw_modification_ids": mods,
        })

    return cards


def crawl_catalog(max_pages=0, include_sitemap=False):
    if include_sitemap:
        urls = sorted(sitemap_urls())
        total_urls = len(urls)
        if max_pages:
            urls = urls[:max_pages]

        cards_by_sku = {}
        http_errors = []
        product_pages = 0

        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = {pool.submit(fetch_url, url): url for url in urls}
            done = 0
            for fut in as_completed(futures):
                url = futures[fut]
                done += 1
                try:
                    _, status, html, final = fut.result()
                except Exception as exc:
                    status, html, final = 0, "", str(exc)

                if status != 200 or not html:
                    if len(http_errors) < 200:
                        http_errors.append({
                            "url": url,
                            "status": status,
                            "detail": clean_text(final)[:300],
                        })
                    continue

                soup = BeautifulSoup(html, "html.parser")
                main = soup.select_one("div.product_info.js_product_item[data-element-id]")
                if main:
                    product_pages += 1
                    for card in parse_product(url, html):
                        cards_by_sku[card["sku"]] = card

                if done % 100 == 0:
                    print(
                        f"Sitemap: проверено {done}/{len(urls)}; "
                        f"товарных страниц {product_pages}; карточек {len(cards_by_sku)}"
                    )

        return list(cards_by_sku.values()), {
            "pages_fetched": len(urls),
            "product_pages": product_pages,
            "sitemap_seed_count": total_urls,
            "http_errors": http_errors,
            "remaining_queue": max(0, total_urls - len(urls)),
            "page_limit_hit": bool(max_pages and total_urls > len(urls)),
        }

    # Для регулярной синхронизации идём по актуальным ссылкам каталога.
    # Sitemap содержит тысячи исторических/дублирующих URL и используется только для отдельного аудита.
    seeds = {BASE_URL}
    if include_sitemap:
        seeds.update(sitemap_urls())

    queue = deque(sorted(seeds))
    queued = set(queue)
    fetched = set()
    cards_by_sku = {}
    http_errors = []
    product_pages = 0
    max_workers = 10

    while queue:
        if max_pages and len(fetched) >= max_pages:
            break

        batch = []
        while queue and len(batch) < max_workers:
            url = queue.popleft()
            if url in fetched:
                continue
            if max_pages and len(fetched) + len(batch) >= max_pages:
                break
            batch.append(url)

        if not batch:
            break

        results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(fetch_url, url): url for url in batch}
            for fut in as_completed(futures):
                url = futures[fut]
                try:
                    results[url] = fut.result()
                except Exception as exc:
                    results[url] = (url, 0, "", str(exc))

        for url in batch:
            fetched.add(url)
            _, status, html, final = results[url]
            if status != 200 or not html:
                if len(http_errors) < 200:
                    http_errors.append({"url": url, "status": status, "detail": clean_text(final)[:300]})
                continue

            soup = BeautifulSoup(html, "html.parser")
            main = soup.select_one("div.product_info.js_product_item[data-element-id]")
            if main:
                product_pages += 1
                for card in parse_product(url, html):
                    cards_by_sku[card["sku"]] = card

            for a in soup.find_all("a", href=True):
                cu = canonical_url(a.get("href"), url)
                if not cu or not is_catalog_url(cu):
                    continue
                # Avoid obvious sorting/filter duplicates while retaining ordinary pagination/subcategories.
                path = urlparse(cu).path.casefold()
                if re.search(r"/sort\d+/$", path):
                    continue
                if cu not in fetched and cu not in queued:
                    queued.add(cu)
                    queue.append(cu)

    return list(cards_by_sku.values()), {
        "pages_fetched": len(fetched),
        "product_pages": product_pages,
        "sitemap_seed_count": len(seeds),
        "http_errors": http_errors,
        "remaining_queue": len(queue),
        "page_limit_hit": bool(max_pages and len(fetched) >= max_pages and queue),
    }


class KitClient:
    def __init__(self, token):
        self.session = requests.Session()
        self.token = token
        self.last_request_at = 0.0

    def request(self, method, path, *, params=None, body=None, files=None, timeout=120):
        url = path if path.startswith("http") else KIT_API + path
        for attempt in range(15):
            delay = 0.58 - (time.monotonic() - self.last_request_at)
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
                r = self.session.request(
                    method,
                    url,
                    params=params,
                    json=body,
                    files=files,
                    headers=headers,
                    timeout=timeout,
                )
            except requests.RequestException:
                if attempt == 14:
                    raise
                time.sleep(min(15, attempt + 1))
                continue

            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After") or min(15, 2 + attempt)))
                continue
            if r.status_code >= 500:
                if attempt == 14:
                    r.raise_for_status()
                time.sleep(min(15, 1 + attempt))
                continue
            r.raise_for_status()
            return r.json() if r.content else {}
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
                for value in payload.values():
                    if isinstance(value, list):
                        batch = value
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
            "POST",
            "/v1/characteristics",
            body={"title": title, "type": "STRING", "select_mode": "SINGLE"},
        )

    def create_product(self, category_id):
        return self.request("POST", "/v1/products", body={"category_ids": [category_id]})

    def create_variant(self, body):
        return self.request("POST", "/v1/variants", body=body, timeout=180)

    def get_variant(self, variant_id):
        return self.request("GET", f"/v1/variants/{variant_id}")

    def patch_variant(self, variant_id, body):
        return self.request("PATCH", f"/v1/variants/{variant_id}", body=body, timeout=150)

    def archive_variant(self, variant_id):
        return self.request("POST", f"/v1/variants/{variant_id}/archive")

    def update_prices(self, items):
        if not items:
            return
        for start in range(0, len(items), 1000):
            self.request(
                "POST",
                "/v1/variants/prices/bulk_update",
                body={"items": items[start:start + 1000]},
                timeout=180,
            )

    def update_stocks(self, items):
        if not items:
            return
        for start in range(0, len(items), 3000):
            self.request(
                "POST",
                "/v1/variants/stocks/bulk_update",
                body={"items": items[start:start + 3000]},
                timeout=180,
            )

    def upload_image_url(self, url):
        src = requests.get(url, timeout=120, headers={"User-Agent": UA})
        src.raise_for_status()
        ctype = (
            src.headers.get("Content-Type")
            or mimetypes.guess_type(urlparse(url).path)[0]
            or "image/jpeg"
        )
        ext = os.path.splitext(urlparse(url).path)[1] or ".jpg"
        return self.request(
            "POST",
            "/v1/files",
            files={"file": ("fh-mebel" + ext[:10], src.content, ctype)},
            timeout=180,
        )


def exact_title(rows, title, parent_id=None):
    matches = [
        row for row in rows
        if norm(row.get("title")) == norm(title)
        and (parent_id is None or s(row.get("parent_id")) == s(parent_id))
    ]
    return matches[0] if matches else None


def variant_rows(payload):
    rows = payload.get("variants") or payload.get("items") or payload.get("results") or []
    if isinstance(rows, dict):
        rows = rows.get("items") or []
    return [x for x in rows if isinstance(x, dict)]


def prepare_media(kit, card, report):
    media = []
    for url in card["pictures"]:
        try:
            uploaded = kit.upload_image_url(url)
            file_id = s(uploaded.get("id"))
            if file_id:
                media.append({
                    "type": "IMAGE",
                    "display_sequence": len(media),
                    "image_id": file_id,
                })
                report["images_uploaded"] += 1
        except Exception as exc:
            report["image_errors"] += 1
            if len(report["warnings"]) < 300:
                report["warnings"].append({
                    "sku": card["sku"],
                    "stage": "image",
                    "url": url,
                    "message": str(exc)[:500],
                })
    return media


def save_report(report):
    HERE.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def run(dry_run=False, force=False, max_pages=0, max_items=0, include_sitemap=False):
    started = iso_now()
    report = {
        "status": "ВЫПОЛНЯЕТСЯ",
        "started_at": started,
        "dry_run": bool(dry_run),
        "source": BASE_URL,
        "supplier": SUPPLIER,
        "price_rule": "Цена продажи в KIT = текущая цена на fh-mebel.ru",
        "stock_rule": f"{WAREHOUSE_NAME} = {STOCK_QTY} шт. для каждой карточки",
        "modification_rule": "Каждая комбинация модификации создаётся отдельной карточкой KIT",
        "sku_rule": "FH-<FH element id>-<FH price id>; fallback: hash параметров",
        "schedule_rule": "Не чаще одного успешного live-запуска за 14 дней",
        "crawl_mode": "актуальные категории/товарные ссылки" if not include_sitemap else "аудит + sitemap",
        "crawl": {},
        "source_cards": 0,
        "source_base_products": 0,
        "cards_with_modifications": 0,
        "created": 0,
        "would_create": 0,
        "updated": 0,
        "would_update": 0,
        "duplicate_variants_archived": 0,
        "price_updates": 0,
        "stock_updates": 0,
        "images_uploaded": 0,
        "image_errors": 0,
        "errors": [],
        "warnings": [],
        "sample": [],
        "complete": False,
    }

    if not dry_run:
        due, age = due_for_live(force)
        if not due:
            report["status"] = "ПРОПУЩЕНО"
            report["reason"] = "С последнего успешного обновления ещё не прошло 14 дней"
            report["seconds_since_last_success"] = int(age or 0)
            report["complete"] = True
            report["finished_at"] = iso_now()
            save_report(report)
            return 0

    cards, crawl = crawl_catalog(max_pages=max_pages, include_sitemap=include_sitemap)
    report["crawl"] = crawl
    if max_items:
        cards = cards[:max_items]

    report["source_cards"] = len(cards)
    report["source_base_products"] = len({x["source_element_id"] for x in cards})
    report["cards_with_modifications"] = sum(1 for x in cards if x["modifications"])
    report["sample"] = [
        {
            "sku": x["sku"],
            "name": x["name"],
            "price": x["price"],
            "manufacturer": x["manufacturer"],
            "modifications": x["modifications"],
            "category_path": x["category_path"],
            "pictures": len(x["pictures"]),
            "source_url": x["source_url"],
        }
        for x in cards[:30]
    ]

    if not cards:
        report["status"] = "ОШИБКА"
        report["errors"].append({"stage": "crawl", "message": "Товары fh-mebel.ru не найдены"})
        report["finished_at"] = iso_now()
        save_report(report)
        return 2

    if crawl.get("page_limit_hit") and not dry_run:
        report["status"] = "ОШИБКА"
        report["errors"].append({
            "stage": "crawl",
            "message": "Достигнут max-pages; live-импорт остановлен, чтобы не загрузить неполный каталог",
        })
        report["finished_at"] = iso_now()
        save_report(report)
        return 2

    if dry_run:
        report["would_create"] = len(cards)
        report["status"] = "ПРОВЕРКА УСПЕШНА"
        report["complete"] = True
        report["finished_at"] = iso_now()
        save_report(report)
        return 0

    token = s(os.environ.get("YANDEX_KIT_TOKEN"))
    if not token:
        report["status"] = "ОШИБКА"
        report["errors"].append({"stage": "KIT", "message": "YANDEX_KIT_TOKEN не настроен"})
        report["finished_at"] = iso_now()
        save_report(report)
        return 2

    kit = KitClient(token)

    warehouses = kit.list_all("/v1/warehouses", {"status": "ACTIVE"}, "warehouses")
    wh = exact_title(warehouses, WAREHOUSE_NAME)
    if not wh:
        report["status"] = "ОШИБКА"
        report["errors"].append({
            "stage": "KIT",
            "message": f"Склад с точным названием {WAREHOUSE_NAME!r} не найден",
            "available_warehouses": [s(x.get("title")) for x in warehouses],
        })
        report["finished_at"] = iso_now()
        save_report(report)
        return 2
    warehouse_id = s(wh.get("id"))

    categories = kit.list_all("/v1/categories", {"status": "ACTIVE"}, "categories")

    def ensure_category(title, parent_id=""):
        row = exact_title(categories, title, parent_id)
        if row:
            return s(row.get("id"))
        created = kit.create_category(title, parent_id or None)
        cid = s(created.get("id"))
        if not cid:
            raise RuntimeError(f"KIT не вернул id категории {title!r}")
        normalized = dict(created)
        normalized.setdefault("parent_id", parent_id)
        categories.append(normalized)
        return cid

    root_category_id = ensure_category(ROOT_CATEGORY)
    category_cache = {}

    def card_category(card):
        path = [clean_text(x) for x in card["category_path"] if clean_text(x)]
        if not path:
            return root_category_id
        parent = root_category_id
        full = []
        for title in path:
            full.append(title)
            key = tuple(full)
            if key not in category_cache:
                category_cache[key] = ensure_category(title, parent)
            parent = category_cache[key]
        return parent

    characteristics = kit.list_all("/v1/characteristics", {"status": "ACTIVE"}, "characteristics")
    char_cache = {}

    def ensure_char(title):
        title = clean_text(title)
        key = norm(title)
        if key in char_cache:
            return char_cache[key]
        matches = [x for x in characteristics if norm(x.get("title")) == key]
        if matches:
            chosen = next(
                (x for x in matches if s(x.get("type")).upper() in ("STRING", "MULTIPLE_STRING")),
                matches[0],
            )
            cid = s(chosen.get("id"))
            char_cache[key] = cid
            return cid
        created = kit.create_characteristic(title)
        cid = s(created.get("id"))
        if not cid:
            raise RuntimeError(f"KIT не вернул id характеристики {title!r}")
        characteristics.append(created)
        char_cache[key] = cid
        return cid

    def char_payload(card):
        pairs = {
            "Поставщик": SUPPLIER,
            "Источник": card["source_url"],
            "FH Mebel ID": card["source_element_id"],
            "FH Price ID": card["source_price_id"],
            "Артикул": card["sku"],
            "Код для сайта": card["sku"],
            "Состав комплекта": card.get("composition", ""),
        }
        for title, value in card["characteristics"].items():
            if not clean_text(value):
                continue
            if norm(title) in {"артикул", "артикул товара", "код товара"}:
                pairs["Артикул поставщика"] = clean_text(value)
            else:
                pairs[clean_text(title)] = clean_text(value)
        for title, value in card["modifications"].items():
            if clean_text(value):
                pairs[clean_text(title)] = clean_text(value)

        rows = []
        for title, value in pairs.items():
            if not clean_text(value):
                continue
            cid = ensure_char(title)
            rows.append({
                "characteristic_id": cid,
                "value": clean_text(value),
                "values": [clean_text(value)],
            })
        return rows

    def find_exact(sku):
        payload = kit.request(
            "GET", "/v1/variants",
            params={"name": sku, "page": 1, "per_page": 100},
        )
        return [
            row for row in variant_rows(payload)
            if s(row.get("sku")) == sku and s(row.get("status")).upper() != "ARCHIVED"
        ]

    price_batch = []
    stock_batch = []

    for idx, card in enumerate(cards, start=1):
        try:
            exact = find_exact(card["sku"])
            if len(exact) > 1:
                exact = sorted(
                    exact,
                    key=lambda row: (
                        int(row.get("kit_id") or 10**18),
                        s(row.get("created_at")),
                        s(row.get("id")),
                    ),
                )
                keeper = exact[0]
                for duplicate in exact[1:]:
                    did = s(duplicate.get("id"))
                    if did:
                        kit.archive_variant(did)
                        report["duplicate_variants_archived"] += 1
                exact = [keeper]

            chars = char_payload(card)
            price = s(card["price"])
            pricing = {"price": price, "manual_discount_price": price}
            stock_rows = [{
                "warehouse_id": warehouse_id,
                "quantity": STOCK_QTY,
                "reserved": 0,
            }]

            if exact:
                variant = kit.get_variant(s(exact[0].get("id")))
                vid = s(variant.get("id"))
                patch = {
                    "name": card["name"],
                    "description": card["description"],
                    "characteristics": chars,
                }
                if card["manufacturer"]:
                    patch["brand"] = card["manufacturer"]

                if not (variant.get("media") or []) and card["pictures"]:
                    media = prepare_media(kit, card, report)
                    if media:
                        patch["media"] = media

                kit.patch_variant(vid, patch)
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
                report["updated"] += 1
            else:
                category_id = card_category(card)
                product = kit.create_product(category_id)
                product_id = s(product.get("id"))
                if not product_id:
                    raise RuntimeError("KIT не вернул product_id")

                media = prepare_media(kit, card, report)
                body = {
                    "sku": card["sku"],
                    "name": card["name"],
                    "description": card["description"],
                    "status": "PUBLISHED",
                    "product_id": product_id,
                    "pricing": pricing,
                    "stocks": stock_rows,
                    "characteristics": chars,
                }
                if card["manufacturer"]:
                    body["brand"] = card["manufacturer"]
                if media:
                    body["media"] = media

                created = kit.create_variant(body)
                vid = s(created.get("id"))
                if not vid:
                    raise RuntimeError("KIT не вернул variant_id")
                report["created"] += 1

            report["price_updates"] += 1
            report["stock_updates"] += 1

            if len(price_batch) >= 100:
                kit.update_prices(price_batch)
                price_batch.clear()
            if len(stock_batch) >= 300:
                kit.update_stocks(stock_batch)
                stock_batch.clear()

        except Exception as exc:
            report["errors"].append({
                "sku": card["sku"],
                "source_url": card["source_url"],
                "message": str(exc)[:1200],
            })
            if len(report["errors"]) >= 200:
                break

    if price_batch:
        kit.update_prices(price_batch)
    if stock_batch:
        kit.update_stocks(stock_batch)

    report["complete"] = (report["created"] + report["updated"] == len(cards) and not report["errors"])
    report["status"] = "УСПЕШНО" if report["complete"] else "ЗАВЕРШЕНО С ОШИБКАМИ"
    report["finished_at"] = iso_now()

    if report["complete"]:
        STATE_PATH.write_text(
            json.dumps({
                "last_success_at": report["finished_at"],
                "source_cards": len(cards),
                "supplier": SUPPLIER,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    save_report(report)
    return 0 if report["complete"] else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--max-pages", type=int, default=0)
    ap.add_argument("--max-items", type=int, default=0)
    ap.add_argument("--include-sitemap", action="store_true")
    args = ap.parse_args()
    return run(
        dry_run=args.dry_run,
        force=args.force,
        max_pages=max(0, args.max_pages),
        max_items=max(0, args.max_items),
        include_sitemap=args.include_sitemap,
    )


if __name__ == "__main__":
    raise SystemExit(main())
