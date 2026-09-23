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
    current = None
    old = None
    scope = product_scope(soup, h1)
    for node in scope.find_all(True, class_=re.compile(r"price", re.I)):
        tx = clean_text(node.get_text(" ", strip=True))
        d = money(tx) if ("₽" in tx or "руб" in norm(tx)) else None
        if d is None:
            continue
        cls = " ".join(node.get("class", []))
        ncls = norm(cls)
        if any(k in ncls for k in ("old", "base", "original", "without", "cross")):
            if old is None or d > old:
                old = d
        elif current is None:
            current = d

    segment = text_after_h1_until(
        soup, h1,
        ["необходима предоплата", "доставка по городу", "основные характеристики"],
    )
    nums = []
    for m in re.finditer(r"(\d[\d\s]{0,14}(?:[,.]\d{1,2})?)\s*(?:₽|руб(?:\.|лей|ля)?)", segment, flags=re.I):
        d = money(m.group(1))
        if d is not None and d >= 50:
            nums.append(d)
    nums = unique(str(x) for x in nums)
    nums = [Decimal(x) for x in nums]
    if current is None and nums:
        current = nums[0]
    if old is None:
        for d in nums[1:4]:
            if current is not None and d >= current and d <= current * Decimal("4"):
                old = d
                break
    if current is None:
        return None, None
    if old is None or old < current:
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
    for selector in (
        ".breadcrumb", ".breadcrumbs", "[class*='bread']", "[itemtype*='BreadcrumbList']", "nav[aria-label*='breadcrumb' i]",
    ):
        for node in soup.select(selector):
            if h1 in node.descendants:
                continue
            texts = [clean_text(x.get_text(" ", strip=True)) for x in node.find_all(["a", "span"])]
            texts = [x for x in texts if x and norm(x) not in ("главная", "каталог") and x != clean_text(h1.get_text(" ", strip=True))]
            if texts:
                candidates = texts
                break
        if candidates:
            break
    out = []
    for x in candidates:
        if x not in out and len(x) <= 100:
            out.append(x)
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
        for node in start.next_elements:
            if getattr(node, "name", None) in ("h2", "h3"):
                txh = clean_text(node.get_text(" ", strip=True))
                if txh and "основные характеристики" not in norm(txh):
                    break
            if getattr(node, "name", None) not in ("p", "dt", "dd", "td", "span", "div"):
                continue
            if getattr(node, "find", None) and node.find(["p", "dt", "dd", "td", "div"], recursive=False):
                continue
            tx = clean_text(node.get_text(" ", strip=True))
            if not tx or len(tx) > 300:
                continue
            low = norm(tx)
            if low in CONTROL_TEXT:
                continue
            if low.startswith("свернуть") or low.startswith("развернуть"):
                continue
            if tx not in tokens:
                tokens.append(tx)
            if len(tokens) > 160:
                break

        i = 0
        while i + 1 < len(tokens):
            label = clean_text(tokens[i]).rstrip(":")
            value = clean_text(tokens[i + 1])
            if is_probable_label(label) and value and len(value) <= 250:
                if norm(value) not in CONTROL_TEXT and not is_probable_label(value):
                    pairs.setdefault(label, value)
                    i += 2
                    continue
                if not any(k in norm(value) for k in ("характеристик", "развернуть", "свернуть")):
                    pairs.setdefault(label, value)
                    i += 2
                    continue
            i += 1

    for tr in soup.find_all("tr"):
        cells = [clean_text(x.get_text(" ", strip=True)) for x in tr.find_all(["th", "td"], recursive=False)]
        if len(cells) >= 2 and is_probable_label(cells[0]) and cells[1]:
            pairs[cells[0].rstrip(":")] = cells[1]
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            lab, val = clean_text(dt.get_text(" ", strip=True)), clean_text(dd.get_text(" ", strip=True))
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
