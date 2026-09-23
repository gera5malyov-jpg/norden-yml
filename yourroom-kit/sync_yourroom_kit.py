#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import io
import json
import mimetypes
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import cv2
import numpy as np
import requests
from bs4 import BeautifulSoup
from PIL import Image

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE.parent / "aletan-kit" / "sync_aletan_kit.py"
REPORT_PATH = HERE / "last_sync_report.json"
STATE_PATH = HERE / "state.json"

BASE_URL = "https://yourroom.ru/"
SITEMAP_URL = "https://yourroom.ru/sitemap_prods.xml"
KIT_API = "https://api.kit.yandex.net"
SUPPLIER = "ВашаКомната"
ROOT_CATEGORY = "ВашаКомната"
WAREHOUSE_NAME = "СПБ"
STOCK_QTY = 100
SKU_PREFIX = "YOU-"
MIN_INTERVAL_DAYS = 14
CRAWL_DELAY_SECONDS = 1.05
MAX_IMAGES_PER_CARD = 12

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "Chrome/153 Safari/537.36 YourRoom-KIT-Sync"
)

SOURCE_NAME_RE = re.compile(
    r"(?:ваша\s*комната(?:\.\s*рф)?|вашакомната(?:\.\s*рф)?|yourroom\.ru)",
    re.I,
)
CONTROL_TEXT = {
    "все характеристики", "скрыть характеристики", "развернуть текст",
    "свернуть текст", "отзывы", "вернуться в каталог", "перейти в корзину",
}


def load_base():
    spec = importlib.util.spec_from_file_location("yourroom_kit_base", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {BASE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


base = load_base()


def s(v):
    return str(v or "").strip()


def clean_text(v):
    return " ".join(s(v).replace("\xa0", " ").split())


def norm(v):
    return clean_text(v).casefold().replace("ё", "е")


def money(v):
    raw = clean_text(v).replace(" ", "").replace(",", ".")
    raw = re.sub(r"[^0-9.]", "", raw)
    if not raw:
        return None
    try:
        d = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return d if d > 0 else None


def currency_amounts(text):
    text = s(text).replace("\xa0", " ")
    pattern = re.compile(
        r"(?<!\d)(\d{1,3}(?:\s\d{3})+(?:[,.]\d{1,2})?|\d{2,7}(?:[,.]\d{1,2})?)\s*(?:₽|руб(?:\.|лей|ля)?)",
        re.I,
    )
    out = []
    for m in pattern.finditer(text):
        d = money(m.group(1))
        if d is not None and d >= Decimal("50") and d <= Decimal("100000000"):
            out.append(d)
    return out

def ruble(v):
    if v is None:
        return None
    return Decimal(v).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def unique(values):
    out, seen = [], set()
    for value in values:
        value = s(value)
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def canonical_product_url(url):
    p = urlparse(urljoin(BASE_URL, s(url)))
    if p.scheme not in ("http", "https"):
        return ""
    if p.netloc.casefold() not in ("yourroom.ru", "www.yourroom.ru"):
        return ""
    path = re.sub(r"/{2,}", "/", p.path or "/")
    if not path.startswith("/products/"):
        return ""
    return urlunparse(("https", "yourroom.ru", path.rstrip("/"), "", "", ""))


def load_state():
    if not STATE_PATH.exists():
        return {"items": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"items": {}}
        if not isinstance(data.get("items"), dict):
            data["items"] = {}
        return data
    except Exception:
        return {"items": {}}


def save_state(state):
    HERE.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def due_for_live(force=False):
    if force:
        return True, None
    state = load_state()
    raw = s(state.get("last_success_at"))
    if not raw:
        return True, None
    try:
        last = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - last.astimezone(timezone.utc)).total_seconds() / 86400
        return age >= MIN_INTERVAL_DAYS, age
    except Exception:
        return True, None


def write_report(report):
    HERE.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


class ThrottledSession:
    def __init__(self):
        self.ses = requests.Session()
        self.ses.headers.update({
            "User-Agent": UA,
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.4",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        self.last_at = 0.0

    def get(self, url, *, timeout=60, binary=False):
        last_exc = None
        for attempt in range(5):
            delay = CRAWL_DELAY_SECONDS - (time.monotonic() - self.last_at)
            if delay > 0:
                time.sleep(delay)
            self.last_at = time.monotonic()
            try:
                r = self.ses.get(url, timeout=timeout, allow_redirects=True)
                if r.status_code == 429:
                    time.sleep(float(r.headers.get("Retry-After") or min(20, 3 + attempt * 3)))
                    continue
                if r.status_code >= 500:
                    time.sleep(min(15, 2 + attempt * 2))
                    continue
                return r
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(min(10, 2 + attempt * 2))
        raise RuntimeError(str(last_exc or f"Не удалось скачать {url}"))


WEB = ThrottledSession()


def verify_spb_context():
    r = WEB.get(BASE_URL, timeout=60)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    text = clean_text(soup.get_text(" ", strip=True))
    title = clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    hay = norm(title + " " + text[:5000])
    if "санкт-петербург" not in hay:
        raise RuntimeError(
            "Сайт открылся не в регионе Санкт-Петербург. Импорт остановлен, чтобы не загрузить цены другого города."
        )


def sitemap_product_urls():
    r = WEB.get(SITEMAP_URL, timeout=120)
    r.raise_for_status()
    urls = re.findall(r"<loc>\s*(.*?)\s*</loc>", r.text, flags=re.I | re.S)
    if not urls:
        urls = re.findall(r"https?://yourroom\.ru/products/[^<\s]+", r.text, flags=re.I)
    return unique(canonical_product_url(html.unescape(u)) for u in urls if canonical_product_url(html.unescape(u)))


def drop_source_mentions(text):
    text = clean_text(text)
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+|\s*[\r\n]+\s*", text)
    kept = []
    for part in parts:
        part = clean_text(part)
        if not part:
            continue
        if SOURCE_NAME_RE.search(part):
            continue
        if re.search(r"\b(?:8|\+7)\s*\(?812\)?", part):
            continue
        kept.append(part)
    cleaned = clean_text(" ".join(kept))
    cleaned = SOURCE_NAME_RE.sub("", cleaned)
    return clean_text(cleaned)


def text_after_h1_until(soup, h1, stop_words):
    values = []
    for node in h1.next_elements:
        if getattr(node, "name", None) in ("script", "style"):
            continue
        if isinstance(node, str):
            tx = clean_text(node)
            if not tx:
                continue
            low = norm(tx)
            if any(sw in low for sw in stop_words):
                break
            values.append(tx)
    return clean_text(" ".join(values))


def extract_prices(soup, h1):
    segment = text_after_h1_until(
        soup, h1,
        ["необходима предоплата", "доставка по городу", "основные характеристики"],
    )
    nums = currency_amounts(segment)
    if not nums:
        return None, None

    current = nums[0]
    old = None
    for d in nums[1:]:
        if d >= current and d <= current * Decimal("4"):
            old = d
            break

    if current < Decimal("50") or current > Decimal("100000000"):
        return None, None
    if old is None:
        old = current
    return ruble(current), ruble(old)

def product_scope(soup, h1):
    for ancestor in h1.parents:
        if getattr(ancestor, "name", None) not in ("div", "main", "section", "article", "body"):
            continue
        raw = str(ancestor)
        if "/shopfiles/img_products/" in raw and ("₽" in clean_text(ancestor.get_text(" ", strip=True)) or "руб" in norm(ancestor.get_text(" ", strip=True))):
            if len(raw) < 2_000_000:
                return ancestor
    return soup


def breadcrumb_path(soup, h1):
    candidates = []
    product_name = clean_text(h1.get_text(" ", strip=True))
    for selector in (
        ".breadcrumb", ".breadcrumbs", "[class*='bread']", "[itemtype*='BreadcrumbList']", "nav[aria-label*='breadcrumb' i]",
    ):
        for node in soup.select(selector):
            if h1 in node.descendants:
                continue
            texts = [clean_text(x.get_text(" ", strip=True)) for x in node.find_all(["a", "span"])]
            texts = [x for x in texts if x and norm(x) not in ("главная", "каталог") and x != product_name]
            if texts:
                candidates = texts
                break
        if candidates:
            break

    out = []
    for x in candidates:
        if x not in out and len(x) <= 100:
            out.append(x)

    while out:
        last = norm(out[-1])
        pname = norm(product_name)
        if last and (pname.startswith(last) or last.startswith(pname)):
            out.pop()
        else:
            break
    return out[:5]

def is_probable_label(text):
    text = clean_text(text).rstrip(":")
    if not text or len(text) > 90:
        return False
    low = norm(text)
    if low in CONTROL_TEXT:
        return False
    if SOURCE_NAME_RE.search(text):
        return False
    if not re.search(r"[A-Za-zА-Яа-яЁё]", text):
        return False
    if re.match(r"^(?:да|нет|россия|беларусь|китай|турция|\d)", low):
        return False
    if any(x in low for x in ("доставка", "самовывоз", "оплата", "сборка", "подъем на этаж", "магазин")):
        return False
    return True


def extract_characteristics(soup, h1):
    start = soup.find(
        lambda tag: getattr(tag, "name", None) in ("h2", "h3", "div", "span")
        and "основные характеристики" in norm(tag.get_text(" ", strip=True))
    )
    pairs = {}

    if start:
        tokens = []
        for node in start.find_all_next(["p", "h2", "h3"], limit=140):
            if node is start:
                continue
            tx = clean_text(node.get_text(" ", strip=True))
            if not tx:
                continue
            low = norm(tx)

            if node.name in ("h2", "h3") and "основные характеристики" not in low:
                break
            if "лучшие предложения" in low or "товары в наличии" in low:
                break
            if len(tx) > 350:
                # Началось текстовое описание товара.
                break
            if low in CONTROL_TEXT or low.startswith("свернуть") or low.startswith("развернуть"):
                continue
            if any(x in low for x in (
                "доставка по городу", "срочная доставка", "самовывоз",
                "качественная сборка", "подъем на этаж", "оплата наличными",
            )):
                continue
            if tx not in tokens:
                tokens.append(tx)

        i = 0
        while i + 1 < len(tokens):
            label = clean_text(tokens[i]).rstrip(":")
            value = clean_text(tokens[i + 1])
            if is_probable_label(label) and value:
                pairs.setdefault(label, value)
                i += 2
            else:
                i += 1

    for tr in soup.find_all("tr"):
        cells = [clean_text(x.get_text(" ", strip=True)) for x in tr.find_all(["th", "td"], recursive=False)]
        if len(cells) >= 2 and is_probable_label(cells[0]) and cells[1]:
            pairs[cells[0].rstrip(":")] = cells[1]

    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            lab = clean_text(dt.get_text(" ", strip=True))
            val = clean_text(dd.get_text(" ", strip=True))
            if is_probable_label(lab) and val:
                pairs[lab.rstrip(":")] = val

    text = clean_text(soup.get_text(" ", strip=True))
    m = re.search(
        r"Выберите\s+цвет\s+(.+?)(?:\s+-\s+(?:В\s+наличии|Привезем|Под\s+заказ)|\s+Перейти\s+в\s+корзину)",
        text, flags=re.I,
    )
    if m:
        color = clean_text(m.group(1))
        if 1 <= len(color) <= 120:
            pairs.setdefault("Цвет", color)

    cleaned = {}
    for k, v in pairs.items():
        nk = norm(k)
        if any(x in nk for x in ("остаток", "налич", "поставщик", "источник", "доставка", "магазин")):
            continue
        if SOURCE_NAME_RE.search(k) or SOURCE_NAME_RE.search(v):
            continue
        cleaned[clean_text(k)] = clean_text(v)
    return cleaned

def extract_manufacturer(soup, characteristics):
    for k, v in characteristics.items():
        if "производител" in norm(k) and clean_text(v):
            return clean_text(v)
    meta = soup.find("meta", attrs={"name": "description"})
    tx = clean_text(meta.get("content") if meta else "")
    m = re.search(r"Производитель\s+([^.;]{2,100})", tx, flags=re.I)
    return clean_text(m.group(1)) if m else ""


def extract_description(soup, h1):
    selectors = [
        "#prod-description-wrapp", "[id*='prod-description']", "[class*='product-description']",
        "[class*='prod-description']", "[itemprop='description']",
    ]
    candidates = []
    for sel in selectors:
        for node in soup.select(sel):
            tx = clean_text(node.get_text(" ", strip=True))
            if 80 <= len(tx) <= 12000:
                candidates.append(tx)
    if not candidates:
        for p in h1.find_all_next("p", limit=80):
            tx = clean_text(p.get_text(" ", strip=True))
            if len(tx) >= 120 and "лучшие предложения" not in norm(tx):
                candidates.append(tx)
    if not candidates:
        return ""
    best = max(candidates, key=len)
    return drop_source_mentions(best)


def extract_image_urls(soup, h1, page_url):
    scope = product_scope(soup, h1)
    urls = []
    for a in scope.find_all("a", href=True):
        href = s(a.get("href"))
        if "/shopfiles/img_products/" in href and re.search(r"\.(?:jpe?g|png|webp)(?:\?|$)", href, re.I):
            urls.append(urljoin(page_url, href))
    for img in scope.find_all("img"):
        for attr in ("data-src", "data-lazy", "data-original", "src"):
            src = s(img.get(attr))
            if "/shopfiles/img_products/" in src and re.search(r"\.(?:jpe?g|png|webp)(?:\?|$)", src, re.I):
                urls.append(urljoin(page_url, src))
    urls = unique(u.split("#", 1)[0] for u in urls)
    full = [u for u in urls if "/full_" in u or "/full-" in u]
    return (full or urls)[:MAX_IMAGES_PER_CARD]


def parse_product(url, raw_html):
    soup = BeautifulSoup(raw_html, "html.parser")
    h1 = soup.find("h1")
    if not h1:
        return None, "нет H1"
    name = clean_text(h1.get_text(" ", strip=True))
    if not name:
        return None, "пустое название"

    canonical = ""
    can = soup.find("link", rel=lambda x: x and "canonical" in x)
    if can:
        canonical = canonical_product_url(can.get("href"))
    canonical = canonical or canonical_product_url(url)

    customer, old = extract_prices(soup, h1)
    if customer is None:
        return None, "не найдена цена"

    chars = extract_characteristics(soup, h1)
    manufacturer = extract_manufacturer(soup, chars)
    description = extract_description(soup, h1)
    pictures = extract_image_urls(soup, h1, canonical)
    categories = breadcrumb_path(soup, h1)

    return {
        "source_key": canonical,
        "source_url": canonical,
        "name": name,
        "customer_price": customer,
        "old_price": old,
        "manufacturer": manufacturer,
        "description": description,
        "characteristics": chars,
        "pictures": pictures,
        "category_path": categories,
    }, ""


def candidate_original_urls(url):
    p = urlparse(url)
    dirname, basename = os.path.split(p.path)
    stem, ext = os.path.splitext(basename)
    names = [basename]
    if stem.startswith("full_"):
        core = stem[5:]
        names.extend([core + ext, "original_" + core + ext, "orig_" + core + ext, "source_" + core + ext])
    elif stem.startswith("full-"):
        core = stem[5:]
        names.extend([core + ext, "original-" + core + ext, "orig-" + core + ext])
    out = []
    for n in names:
        path = dirname.rstrip("/") + "/" + n
        out.append(urlunparse((p.scheme, p.netloc, path, "", "", "")))
    return unique(out)


def decode_image(content):
    try:
        im = Image.open(io.BytesIO(content)).convert("RGB")
        if im.width < 300 or im.height < 300:
            return None
        return im
    except Exception:
        return None


def watermark_anchor(im):
    arr = np.array(im)
    h, w = arr.shape[:2]
    rgb = arr.astype(np.int16)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    mask = (
        (r >= 150) & (r <= 255) &
        (b >= 85) & (b <= 235) &
        (r - g >= 25) &
        (b - g >= 5)
    ).astype(np.uint8) * 255
    zone = np.zeros_like(mask)
    zone[int(h * 0.32):int(h * 0.86), int(w * 0.04):int(w * 0.55)] = 255
    mask = cv2.bitwise_and(mask, zone)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    best = None
    for i in range(1, n):
        x, y, cw, ch, area = stats[i]
        if area < max(10, int(w * h * 0.00001)):
            continue
        if area > w * h * 0.02:
            continue
        if cw > w * 0.14 or ch > h * 0.16:
            continue
        score = area - abs((x + cw / 2) - w * 0.20) * 0.02 - abs((y + ch / 2) - h * 0.64) * 0.02
        if best is None or score > best[-1]:
            best = (x, y, cw, ch, area, score)
    return best


def remove_source_watermark(im):
    anchor = watermark_anchor(im)
    if not anchor:
        return im, False, True
    x, y, cw, ch, _, _ = anchor
    arr = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
    h, w = arr.shape[:2]
    x0 = max(0, int(x - 0.018 * w))
    y0 = max(0, int(y - 0.045 * h))
    x1 = min(w, int(max(x + cw + 0.23 * w, x0 + 0.18 * w)))
    y1 = min(h, int(y + ch + 0.055 * h))
    mask = np.zeros((h, w), np.uint8)
    cv2.rectangle(mask, (x0, y0), (x1, y1), 255, thickness=-1)
    cleaned = cv2.inpaint(arr, mask, 7, cv2.INPAINT_TELEA)
    out = Image.fromarray(cv2.cvtColor(cleaned, cv2.COLOR_BGR2RGB))
    ok = watermark_anchor(out) is None
    return out, True, ok


def image_bytes(im, source_url):
    ext = os.path.splitext(urlparse(source_url).path)[1].casefold()
    buf = io.BytesIO()
    if ext == ".png":
        im.save(buf, format="PNG", optimize=True)
        ctype = "image/png"
        filename = "yourroom.png"
    else:
        im.save(buf, format="JPEG", quality=92, optimize=True)
        ctype = "image/jpeg"
        filename = "yourroom.jpg"
    return buf.getvalue(), filename, ctype


def clean_product_images(card, report):
    accepted = []
    seen_hashes = set()
    for source_url in card.get("pictures") or []:
        chosen = None
        chosen_mode = ""
        for candidate in candidate_original_urls(source_url):
            try:
                r = WEB.get(candidate, timeout=120)
                if r.status_code != 200 or not r.content:
                    continue
                ctype = norm(r.headers.get("Content-Type"))
                if "image" not in ctype and not re.search(r"\.(?:jpe?g|png|webp)$", urlparse(candidate).path, re.I):
                    continue
                im = decode_image(r.content)
                if im is None:
                    continue
                anchor = watermark_anchor(im)
                if anchor is None:
                    chosen = (im, candidate)
                    chosen_mode = "clean_original" if candidate == source_url else "clean_alternate"
                    break
                cleaned, had_mark, ok = remove_source_watermark(im)
                if had_mark and ok:
                    chosen = (cleaned, candidate)
                    chosen_mode = "watermark_removed"
                    break
            except Exception as exc:
                if len(report["warnings"]) < 200:
                    report["warnings"].append({
                        "source_url": card.get("source_url"),
                        "stage": "image_download",
                        "image_url": candidate,
                        "message": str(exc)[:300],
                    })

        if chosen is None:
            report["images_rejected_watermark_or_error"] += 1
            continue
        im, used_url = chosen
        raw, filename, ctype = image_bytes(im, used_url)
        digest = hashlib.sha1(raw).hexdigest()
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        accepted.append({"bytes": raw, "filename": filename, "content_type": ctype, "source": used_url, "mode": chosen_mode})
        report["images_clean_original"] += int(chosen_mode == "clean_original")
        report["images_clean_alternate"] += int(chosen_mode == "clean_alternate")
        report["images_watermark_removed"] += int(chosen_mode == "watermark_removed")
        if len(accepted) >= MAX_IMAGES_PER_CARD:
            break
    return accepted


class Kit(base.KitClient):
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
        return self.request("POST", "/v1/variants", body=body, timeout=180)

    def patch_variant(self, variant_id, body):
        return self.request("PATCH", f"/v1/variants/{variant_id}", body=body, timeout=180)

    def get_variant(self, variant_id):
        return self.request("GET", f"/v1/variants/{variant_id}")

    def update_stocks(self, items):
        for start in range(0, len(items), 3000):
            self.request(
                "POST", "/v1/variants/stocks/bulk_update",
                body={"items": items[start:start + 3000]}, timeout=180,
            )

    def update_prices(self, items):
        for start in range(0, len(items), 1000):
            self.request(
                "POST", "/v1/variants/prices/bulk_update",
                body={"items": items[start:start + 1000]}, timeout=180,
            )

    def upload_bytes(self, content, filename, content_type):
        endpoint = KIT_API + "/v1/files"
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        for attempt in range(8):
            delay = 0.75 - (time.monotonic() - self.last_request_at)
            if delay > 0:
                time.sleep(delay)
            self.last_request_at = time.monotonic()
            r = self.session.post(
                endpoint, headers=headers,
                files={"file": (filename, content, content_type)}, timeout=180,
            )
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == 7:
                    r.raise_for_status()
                time.sleep(float(r.headers.get("Retry-After") or min(15, 2 + attempt * 2)))
                continue
            r.raise_for_status()
            return r.json() if r.content else {}
        raise RuntimeError("KIT file upload retries exhausted")


def exact_title(rows, title, parent_id=None):
    for row in rows:
        if norm(row.get("title")) != norm(title):
            continue
        if parent_id is not None and s(row.get("parent_id")) != s(parent_id):
            continue
        return row
    return None


def upload_media(kit, cleaned_images, report):
    media = []
    for img in cleaned_images:
        try:
            up = kit.upload_bytes(img["bytes"], img["filename"], img["content_type"])
            file_id = s(up.get("id"))
            if not file_id:
                continue
            media.append({"type": "IMAGE", "display_sequence": len(media), "image_id": file_id})
            report["images_uploaded"] += 1
        except Exception as exc:
            report["image_upload_errors"] += 1
            if len(report["warnings"]) < 200:
                report["warnings"].append({"stage": "kit_image_upload", "message": str(exc)[:400]})
    return media


def crawl_products(urls, max_items, report):
    cards = []
    source_keys = set(urls)
    parse_errors = []
    target = urls[:max_items] if max_items else urls
    for idx, url in enumerate(target, start=1):
        try:
            r = WEB.get(url, timeout=75)
            if r.status_code in (404, 410):
                parse_errors.append({"url": url, "status": r.status_code, "message": "страница отсутствует"})
                continue
            r.raise_for_status()
            card, err = parse_product(r.url, r.text)
            if card:
                cards.append(card)
                source_keys.add(card["source_key"])
            else:
                parse_errors.append({"url": url, "status": r.status_code, "message": err})
        except Exception as exc:
            parse_errors.append({"url": url, "message": str(exc)[:500]})
        if idx % 50 == 0:
            print(f"YourRoom: прочитано {idx}/{len(target)} страниц", flush=True)
    report["parse_errors"] = parse_errors[:300]
    report["product_pages_requested"] = len(target)
    report["product_cards_parsed"] = len(cards)
    return cards, source_keys


def run(dry_run=False, force=False, max_items=0):
    report = {
        "status": "ВЫПОЛНЯЕТСЯ",
        "started_at": iso_now(),
        "dry_run": bool(dry_run),
        "source": BASE_URL,
        "supplier": SUPPLIER,
        "region": "Санкт-Петербург",
        "schedule_rule": "раз в 14 дней",
        "price_rule": {
            "Цена для покупателя": "актуальная цена со скидкой на yourroom.ru",
            "Цена до скидки": "зачеркнутая цена yourroom.ru; если ее нет — равна текущей цене",
        },
        "stock_rule": f"{WAREHOUSE_NAME} = {STOCK_QTY} шт.",
        "sku_rule": "YOU-<код KIT>",
        "modification_rule": "каждая отдельная /products/ страница/модификация — отдельная карточка KIT",
        "missing_rule": "если URL исчез из полного sitemap — остаток 0",
        "description_rule": "упоминания ВашаКомната/Вашакомната.РФ/yourroom.ru удаляются",
        "photo_rule": "используются только изображения без водяного знака; сначала ищется чистый оригинал, затем пробуется программное удаление; без пригодного фото карточка не создается",
        "sitemap_urls": 0,
        "product_pages_requested": 0,
        "product_cards_parsed": 0,
        "created": 0,
        "updated": 0,
        "zeroed_missing": 0,
        "skipped_no_clean_photo": 0,
        "skipped_no_price": 0,
        "price_updates": 0,
        "stock_updates": 0,
        "images_clean_original": 0,
        "images_clean_alternate": 0,
        "images_watermark_removed": 0,
        "images_rejected_watermark_or_error": 0,
        "images_uploaded": 0,
        "image_upload_errors": 0,
        "parse_errors": [],
        "errors": [],
        "warnings": [],
        "sample": [],
        "complete": False,
    }

    if not dry_run:
        due, age = due_for_live(force)
        report["days_since_last_success"] = None if age is None else round(age, 2)
        if not due:
            report["status"] = "ПРОПУЩЕНО"
            report["reason"] = "С последнего успешного обновления прошло менее 14 дней"
            report["complete"] = True
            report["finished_at"] = iso_now()
            write_report(report)
            return 0

    verify_spb_context()
    urls = sitemap_product_urls()
    report["sitemap_urls"] = len(urls)
    if len(urls) < 500:
        raise RuntimeError(f"Подозрительно мало товарных URL в sitemap: {len(urls)}")

    cards, source_keys = crawl_products(urls, max_items, report)
    if not cards:
        raise RuntimeError("Не удалось разобрать ни одной карточки товара")

    prechecked = {}
    check_cards = cards if not dry_run else cards[: min(10, len(cards))]
    for card in check_cards:
        cleaned = clean_product_images(card, report)
        prechecked[card["source_key"]] = cleaned
        report["sample"].append({
            "source_url": card["source_url"],
            "name": card["name"],
            "customer_price": str(card["customer_price"]),
            "old_price": str(card["old_price"]),
            "manufacturer": card["manufacturer"],
            "category_path": card["category_path"],
            "characteristics": dict(list(card["characteristics"].items())[:20]),
            "description_preview": card["description"][:500],
            "source_images": len(card["pictures"]),
            "clean_images": len(cleaned),
            "clean_modes": [x["mode"] for x in cleaned],
        })

    if dry_run:
        report["status"] = "ПРОВЕРКА УСПЕШНА"
        report["complete"] = not report["errors"]
        report["finished_at"] = iso_now()
        write_report(report)
        return 0

    token = s(os.environ.get("YANDEX_KIT_TOKEN"))
    if not token:
        raise RuntimeError("YANDEX_KIT_TOKEN не настроен")
    kit = Kit(token)

    warehouses = kit.list_all("/v1/warehouses", {"status": "ACTIVE"}, "warehouses")
    wh = exact_title(warehouses, WAREHOUSE_NAME)
    if not wh:
        raise RuntimeError(f"Склад KIT {WAREHOUSE_NAME!r} не найден")
    warehouse_id = s(wh.get("id"))
    report["warehouse_id"] = warehouse_id

    categories = kit.list_all("/v1/categories", {"status": "ACTIVE"}, "categories")
    root = exact_title(categories, ROOT_CATEGORY, parent_id="")
    if root:
        root_id = s(root.get("id"))
    else:
        root = kit.create_category(ROOT_CATEGORY)
        root_id = s(root.get("id"))
        categories.append(dict(root, parent_id=""))
    if not root_id:
        raise RuntimeError("Не удалось создать/найти корневую категорию")

    category_cache = {}
    def category_for(card):
        parent = root_id
        path = [x for x in card.get("category_path") or [] if x and not SOURCE_NAME_RE.search(x)]
        for title in path:
            key = (parent, title)
            if key in category_cache:
                parent = category_cache[key]
                continue
            row = exact_title(categories, title, parent_id=parent)
            if row:
                cid = s(row.get("id"))
            else:
                row = kit.create_category(title, parent)
                cid = s(row.get("id"))
                categories.append(dict(row, parent_id=parent))
            category_cache[key] = cid or parent
            parent = category_cache[key]
        return parent

    characteristics = kit.list_all("/v1/characteristics", {"status": "ACTIVE"}, "characteristics")
    char_by_title = {norm(x.get("title")): x for x in characteristics if s(x.get("id"))}
    def ensure_char(title):
        key = norm(title)
        row = char_by_title.get(key)
        if row:
            return s(row.get("id"))
        row = kit.create_characteristic(title)
        cid = s(row.get("id"))
        if not cid:
            raise RuntimeError(f"KIT не вернул id характеристики {title!r}")
        char_by_title[key] = row
        characteristics.append(row)
        return cid

    def char_rows(card):
        rows = []
        for title, value in card.get("characteristics", {}).items():
            title, value = clean_text(title), clean_text(value)
            if not title or not value:
                continue
            nt = norm(title)
            if any(x in nt for x in ("остаток", "налич", "поставщик", "источник", "доставка", "магазин")):
                continue
            if SOURCE_NAME_RE.search(title) or SOURCE_NAME_RE.search(value):
                continue
            cid = ensure_char(title)
            rows.append({"characteristic_id": cid, "value": value, "values": [value]})
        return rows

    state = load_state()
    state_items = state.setdefault("items", {})
    seen_now = set()
    price_batch, stock_batch = [], []

    for idx, card in enumerate(cards, start=1):
        key = card["source_key"]
        seen_now.add(key)
        try:
            cleaned = prechecked.get(key)
            if cleaned is None:
                cleaned = clean_product_images(card, report)
            if not cleaned:
                report["skipped_no_clean_photo"] += 1
                old_state = state_items.get(key) or {}
                vid = s(old_state.get("variant_id"))
                if vid:
                    stock_batch.append({"variant_id": vid, "warehouse_id": warehouse_id, "quantity": 0})
                    report["stock_updates"] += 1
                continue

            current_price = card.get("customer_price")
            old_price = card.get("old_price")
            if current_price is None:
                report["skipped_no_price"] += 1
                continue
            if old_price is None or old_price < current_price:
                old_price = current_price

            mapping = state_items.get(key) or {}
            vid = s(mapping.get("variant_id"))
            variant = None
            if vid:
                try:
                    variant = kit.get_variant(vid)
                    if s(variant.get("status")).upper() == "ARCHIVED":
                        variant = None
                        vid = ""
                except Exception:
                    variant = None
                    vid = ""

            chars = char_rows(card)
            if variant:
                patch = {
                    "name": card["name"],
                    "description": card["description"],
                    "characteristics": chars,
                }
                if card["manufacturer"]:
                    patch["brand"] = card["manufacturer"]
                existing_media = variant.get("media") or []
                existing_images = [m for m in existing_media if s(m.get("type")).upper() == "IMAGE"]
                if len(existing_images) != len(cleaned):
                    fresh_media = upload_media(kit, cleaned, report)
                    preserved = [m for m in existing_media if s(m.get("type")).upper() != "IMAGE"]
                    if fresh_media:
                        patch["media"] = fresh_media + preserved
                kit.patch_variant(vid, patch)
                report["updated"] += 1
            else:
                product = kit.create_product(category_for(card))
                product_id = s(product.get("id"))
                if not product_id:
                    raise RuntimeError("KIT не вернул product_id")
                media = upload_media(kit, cleaned, report)
                if not media:
                    report["skipped_no_clean_photo"] += 1
                    continue
                tmp = hashlib.sha1(key.encode("utf-8")).hexdigest()[:14].upper()
                body = {
                    "sku": f"YOU-TMP-{tmp}",
                    "name": card["name"],
                    "description": card["description"],
                    "status": "PUBLISHED",
                    "product_id": product_id,
                    "pricing": {
                        "price": str(old_price),
                        "manual_discount_price": str(current_price),
                    },
                    "stocks": [{"warehouse_id": warehouse_id, "quantity": STOCK_QTY, "reserved": 0}],
                    "characteristics": chars,
                    "media": media,
                }
                if card["manufacturer"]:
                    body["brand"] = card["manufacturer"]
                created = kit.create_variant(body)
                vid = s(created.get("id"))
                if not vid:
                    raise RuntimeError("KIT не вернул variant_id")
                detail = created if created.get("kit_id") else kit.get_variant(vid)
                kit_code = s(detail.get("kit_id"))
                if kit_code:
                    kit.patch_variant(vid, {"sku": f"{SKU_PREFIX}{kit_code}"})
                    final_sku = f"{SKU_PREFIX}{kit_code}"
                else:
                    final_sku = f"YOU-TMP-{tmp}"
                    report["warnings"].append({
                        "source_url": key, "stage": "final_sku",
                        "message": "KIT не вернул kit_id; временный SKU оставлен до следующего запуска",
                    })
                report["created"] += 1
                state_items[key] = {
                    "variant_id": vid,
                    "sku": final_sku,
                    "created_at": iso_now(),
                }

            state_items.setdefault(key, {})["variant_id"] = vid
            state_items[key]["last_seen_at"] = iso_now()
            state_items[key]["active"] = True

            price_batch.append({
                "variant_id": vid,
                "price": str(old_price),
                "manual_discount_price": str(current_price),
            })
            stock_batch.append({"variant_id": vid, "warehouse_id": warehouse_id, "quantity": STOCK_QTY})
            report["price_updates"] += 1
            report["stock_updates"] += 1

            if len(price_batch) >= 100:
                kit.update_prices(price_batch)
                price_batch.clear()
            if len(stock_batch) >= 300:
                kit.update_stocks(stock_batch)
                stock_batch.clear()
            if idx % 25 == 0:
                save_state(state)
                print(f"YourRoom → KIT: обработано {idx}/{len(cards)}", flush=True)

        except Exception as exc:
            report["errors"].append({
                "source_url": key,
                "name": card.get("name"),
                "message": str(exc)[:1200],
            })
            if len(report["errors"]) >= 250:
                break

    previous_count = int(state.get("last_sitemap_count") or 0)
    safe_full_catalog = not max_items and len(urls) >= 500 and (not previous_count or len(urls) >= int(previous_count * 0.70))

    if safe_full_catalog:
        current_sitemap = set(urls)
        for key, mapping in list(state_items.items()):
            if key in current_sitemap or key in seen_now:
                continue
            vid = s((mapping or {}).get("variant_id"))
            if not vid:
                continue
            stock_batch.append({"variant_id": vid, "warehouse_id": warehouse_id, "quantity": 0})
            mapping["active"] = False
            mapping["missing_since"] = mapping.get("missing_since") or iso_now()
            report["zeroed_missing"] += 1
            report["stock_updates"] += 1
    else:
        report["warnings"].append({
            "stage": "zero_missing",
            "message": "Обнуление исчезнувших отключено: обход был неполным или sitemap подозрительно уменьшился",
        })

    if price_batch:
        kit.update_prices(price_batch)
    if stock_batch:
        kit.update_stocks(stock_batch)

    state["last_sitemap_count"] = len(urls)
    state["supplier"] = SUPPLIER
    state["region"] = "Санкт-Петербург"
    report["complete"] = not report["errors"]
    report["status"] = "УСПЕШНО" if report["complete"] else "ЗАВЕРШЕНО С ОШИБКАМИ"
    report["finished_at"] = iso_now()
    if report["complete"]:
        state["last_success_at"] = report["finished_at"]
    save_state(state)
    write_report(report)
    return 0 if report["complete"] else 2


def main():
    p = argparse.ArgumentParser(description="yourroom.ru (Санкт-Петербург) → Яндекс KIT")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--max-items", type=int, default=0)
    args = p.parse_args()
    try:
        return run(dry_run=args.dry_run, force=args.force, max_items=max(0, args.max_items))
    except Exception as exc:
        write_report({
            "status": "ОШИБКА",
            "complete": False,
            "dry_run": bool(args.dry_run),
            "error": str(exc)[:3000],
            "finished_at": iso_now(),
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
