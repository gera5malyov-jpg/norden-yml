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
