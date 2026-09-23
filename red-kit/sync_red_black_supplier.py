#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

import requests

FEED_URL = "http://opt.red-black.ru/pricelist_api.xml?org=e15e8a25-8153-11e9-8be6-00155d396f01&showcategories=True&showpictures=True&showparams=True"
KIT_API = "https://api.kit.yandex.net"
SKU_PREFIX = "RED-"
ROOT_CATEGORY = "RED-Black"
WAREHOUSE_NAMES = ("МСК", "СПБ привозной")
REPORT_PATH = Path("red-kit/last_supplier_sync.json")
RUNTIME_DIR = Path("red-kit/runtime")
SNAPSHOT_PATH = RUNTIME_DIR / "red_black_snapshot.json"
MIN_FULL_FEED_OFFERS = int(os.getenv("RED_MIN_FULL_FEED_OFFERS", "1000") or "1000")
MAX_NEW_WITHOUT_OVERRIDE = int(os.getenv("RED_MAX_NEW_PRODUCTS", "200") or "200")


def s(value):
    return str(value or "").strip()


def norm(value):
    return " ".join(s(value).casefold().split())


def local_name(tag):
    return str(tag or "").rsplit("}", 1)[-1]


def money(value):
    try:
        d = Decimal(s(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    if d < 0:
        return None
    return d.quantize(Decimal("0.01"))


def money_str(value):
    d = money(value)
    return None if d is None else f"{d:.2f}"


def qty(value):
    try:
        return max(0, int(Decimal(s(value).replace(" ", "").replace(",", "."))))
    except Exception:
        return None


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def canonical_sku(article, code=""):
    article = re.sub(r"\D+", "", s(article))
    if article:
        return f"{SKU_PREFIX}00-{article.zfill(8)}"
    code = s(code).replace("_", "")
    code = re.sub(r"\s+", "", code)
    if code:
        if code.upper().startswith(SKU_PREFIX):
            return code
        return SKU_PREFIX + code
    return ""


def candidate_skus(article, code=""):
    out = []
    canonical = canonical_sku(article, code)
    if canonical:
        out.append(canonical)
    raw_code = s(code)
    if raw_code:
        raw = raw_code if raw_code.upper().startswith(SKU_PREFIX) else SKU_PREFIX + raw_code
        out.append(raw)
        cleaned = raw.replace("_", "")
        out.append(cleaned)
    raw_article = s(article)
    if raw_article:
        out.append(SKU_PREFIX + raw_article)
    return list(dict.fromkeys(x for x in out if x))


def sku_key(value):
    return re.sub(r"\s+", "", s(value)).casefold()


def fetch_feed():
    urls = [FEED_URL]
    if FEED_URL.startswith("http://"):
        urls.append("https://" + FEED_URL[len("http://"):])
    last_error = None
    for url in urls:
        try:
            r = requests.get(
                url,
                timeout=(20, 240),
                headers={"User-Agent": "Mozilla/5.0 Megapolis-RED-KIT/1.0"},
            )
            r.raise_for_status()
            if r.content:
                return url, r.content
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Не удалось скачать полную выгрузку Red Black: {last_error}")


def child_text(node, tag):
    wanted = tag.casefold()
    for child in list(node):
        if local_name(child.tag).casefold() == wanted:
            return s(child.text)
    return ""


def parse_feed():
    effective_url, payload = fetch_feed()
    root = ET.fromstring(payload)

    categories = {}
    for node in root.iter():
        if local_name(node.tag).casefold() != "category":
            continue
        cid = s(node.attrib.get("id"))
        title = s(node.text)
        if cid and title:
            categories[cid] = {
                "id": cid,
                "title": title,
                "parent_id": s(node.attrib.get("parentId")),
            }

    offers = []
    for node in root.iter():
        if local_name(node.tag).casefold() != "offer":
            continue

        article = s(node.attrib.get("article"))
        code = s(node.attrib.get("code"))
        sku = canonical_sku(article, code)
        if not sku:
            continue

        params = {}
        pictures = []
        for child in list(node):
            tag = local_name(child.tag).casefold()
            value = s(child.text)
            if tag == "picture" and value:
                pictures.append(value)
            elif tag == "param":
                title = s(child.attrib.get("name"))
                if title and value:
                    params.setdefault(title, [])
                    if value not in params[title]:
                        params[title].append(value)

        offers.append({
            "source_id": s(node.attrib.get("id")),
            "article": article,
            "code": code,
            "sku": sku,
            "sku_candidates": candidate_skus(article, code),
            "name": child_text(node, "name") or sku,
            "description": child_text(node, "description"),
            "source_url": child_text(node, "url"),
            "category_id": child_text(node, "categoryId"),
            "purchase_price": money_str(child_text(node, "price")),
            "rrc_price": money_str(child_text(node, "rrc_price")),
            "stock": qty(child_text(node, "stock")),
            "pictures": list(dict.fromkeys(pictures)),
            "params": params,
        })

    return effective_url, categories, offers


class KitClient:
    def __init__(self, token):
        self.token = s(token)
        if not self.token:
            raise RuntimeError("YANDEX_KIT_TOKEN не настроен")
        self.session = requests.Session()
        self.last_request_at = 0.0

    def request(self, method, path, *, params=None, body=None, files=None, timeout=120):
        url = path if path.startswith("http") else KIT_API + path
        for attempt in range(15):
            delay = 0.55 - (time.monotonic() - self.last_request_at)
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
                time.sleep(min(15, 1 + attempt))
                continue

            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After") or min(20, 2 + attempt)))
                continue
            if r.status_code >= 500:
                if attempt == 14:
                    r.raise_for_status()
                time.sleep(min(15, 2 + attempt))
                continue

            r.raise_for_status()
            return r.json() if r.content else {}

        raise RuntimeError("KIT API: исчерпаны повторные попытки")

    def list_all(self, path, params=None, preferred_key=None):
        rows = []
        page = 1
        while True:
            q = dict(params or {})
            q.update({"page": page, "per_page": 100})
            payload = self.request("GET", path, params=q)
            batch = payload.get(preferred_key) if preferred_key else None
            if not isinstance(batch, list):
                batch = []
                for key in ("variants", "warehouses", "categories", "characteristics", "items", "results", "data"):
                    if isinstance(payload.get(key), list):
                        batch = payload[key]
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

    def update_prices(self, items):
        for start in range(0, len(items), 1000):
            self.request(
                "POST",
                "/v1/variants/prices/bulk_update",
                body={"items": items[start:start + 1000]},
                timeout=180,
            )

    def update_stocks(self, items):
        for start in range(0, len(items), 2000):
            self.request(
                "POST",
                "/v1/variants/stocks/bulk_update",
                body={"items": items[start:start + 2000]},
                timeout=180,
            )

    def upload_image_url(self, url):
        r = requests.get(url, timeout=(20, 150), headers={"User-Agent": "Mozilla/5.0 Megapolis-RED-KIT/1.0"})
        r.raise_for_status()
        ctype = r.headers.get("Content-Type") or mimetypes.guess_type(urlparse(url).path)[0] or "image/jpeg"
        ext = os.path.splitext(urlparse(url).path)[1] or ".jpg"
        return self.request(
            "POST",
            "/v1/files",
            files={"file": ("redblack" + ext[:10], r.content, ctype)},
            timeout=180,
        )


def write_report(report):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def write_snapshot(snapshot):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def run(dry_run=False):
    effective_url, source_categories, offers = parse_feed()

    report = {
        "started_at": now_iso(),
        "dry_run": bool(dry_run),
        "source": "Red Black",
        "feed_url": effective_url,
        "sku_rule": "RED-00- + артикул поставщика до 8 цифр",
        "purchase_price_rule": "<price> хранится только для таблицы и Webasyst; в KIT не записывается",
        "kit_customer_price_rule": "<rrc_price> = цена для покупателя",
        "stock_rule": "один <stock> поставщика -> одинаковое количество в KIT МСК и СПБ привозной",
        "missing_rule": "исчез из полной выгрузки -> остаток 0, цену не менять",
        "source_offers": len(offers),
        "source_categories": len(source_categories),
        "kit_red_variants": 0,
        "matched_existing": 0,
        "planned_new": 0,
        "created_new": 0,
        "price_updates": 0,
        "stock_updates": 0,
        "missing_zeroed": 0,
        "images_uploaded": 0,
        "image_errors": 0,
        "errors": [],
        "warnings": [],
        "samples_new": [],
        "samples_matched": [],
        "complete": False,
    }

    if len(offers) < MIN_FULL_FEED_OFFERS:
        raise RuntimeError(
            f"Защита от неполной выгрузки: получено {len(offers)} товаров, минимум {MIN_FULL_FEED_OFFERS}. "
            "KIT и Webasyst не изменены."
        )

    bad_stock = [x["sku"] for x in offers if x["stock"] is None]
    if bad_stock:
        raise RuntimeError(
            f"У {len(bad_stock)} товаров отсутствует числовой <stock>. "
            f"Пример: {', '.join(bad_stock[:10])}. Обновление остановлено."
        )

    canonical_groups = defaultdict(list)
    for item in offers:
        canonical_groups[sku_key(item["sku"])].append(item)
    duplicate_source = {k: v for k, v in canonical_groups.items() if len(v) > 1}
    if duplicate_source:
        raise RuntimeError(
            f"В выгрузке найдено {len(duplicate_source)} дублей после формирования RED-SKU; "
            "создание/обновление остановлено."
        )

    token = s(os.getenv("YANDEX_KIT_TOKEN"))
    if not token:
        raise RuntimeError("YANDEX_KIT_TOKEN не настроен")
    kit = KitClient(token)

    warehouses = kit.list_all("/v1/warehouses", {"status": "ACTIVE"}, "warehouses")
    warehouse_ids = {
        s(row.get("title") or row.get("name")): s(row.get("id"))
        for row in warehouses if s(row.get("id"))
    }
    missing_wh = [name for name in WAREHOUSE_NAMES if not warehouse_ids.get(name)]
    if missing_wh:
        raise RuntimeError("В KIT не найдены склады: " + ", ".join(missing_wh))

    variants = kit.list_all("/v1/variants", {"name": SKU_PREFIX}, "variants")
    red_variants = [v for v in variants if s(v.get("sku")).upper().startswith(SKU_PREFIX)]
    report["kit_red_variants"] = len(red_variants)

    kit_by_sku = defaultdict(list)
    for variant in red_variants:
        kit_by_sku[sku_key(variant.get("sku"))].append(variant)

    duplicate_kit = {k: rows for k, rows in kit_by_sku.items() if len(rows) > 1}
    if duplicate_kit:
        report["warnings"].append(f"В KIT есть {len(duplicate_kit)} дублирующихся RED-SKU; конфликтующие позиции пропускаются.")

    resolved = []
    matched_variant_ids = set()
    for item in offers:
        # Канонический RED-SKU имеет приоритет. Старые варианты вида RED-00-_...
        # используются только как fallback, если канонической карточки ещё нет.
        canonical_rows = kit_by_sku.get(sku_key(item["sku"]), [])
        if len(canonical_rows) > 1:
            matches = {s(v.get("id")): v for v in canonical_rows if s(v.get("id"))}
        elif len(canonical_rows) == 1:
            matches = {s(canonical_rows[0].get("id")): canonical_rows[0]}
        else:
            matches = {}
            for candidate in item["sku_candidates"]:
                if sku_key(candidate) == sku_key(item["sku"]):
                    continue
                for variant in kit_by_sku.get(sku_key(candidate), []):
                    vid = s(variant.get("id"))
                    if vid:
                        matches[vid] = variant

        if len(matches) > 1:
            matched_variant_ids.update(matches.keys())
            report["errors"].append({
                "sku": item["sku"],
                "stage": "match",
                "message": "Несколько вариантов KIT соответствуют одному товару: "
                           + ", ".join(sorted(s(v.get("sku")) for v in matches.values())),
            })
            resolved.append((item, None))
            continue

        variant = next(iter(matches.values())) if matches else None
        if variant:
            vid = s(variant.get("id"))
            matched_variant_ids.add(vid)
            item["kit_variant_id"] = vid
            item["kit_sku"] = s(variant.get("sku"))
            report["matched_existing"] += 1
            if len(report["samples_matched"]) < 20:
                report["samples_matched"].append({
                    "source_sku": item["sku"],
                    "kit_sku": item["kit_sku"],
                    "purchase_price": item["purchase_price"],
                    "rrc_price": item["rrc_price"],
                    "stock": item["stock"],
                })
        else:
            report["planned_new"] += 1
            if len(report["samples_new"]) < 30:
                report["samples_new"].append({
                    "sku": item["sku"],
                    "article": item["article"],
                    "code": item["code"],
                    "name": item["name"],
                    "purchase_price": item["purchase_price"],
                    "rrc_price": item["rrc_price"],
                    "stock": item["stock"],
                })
        resolved.append((item, variant))

    if report["planned_new"] > MAX_NEW_WITHOUT_OVERRIDE and os.getenv("RED_ALLOW_MASS_CREATE", "").strip().lower() not in {"1", "true", "yes"}:
        raise RuntimeError(
            f"Защита от массовых дублей: планируется создать {report['planned_new']} новых товаров, "
            f"лимит {MAX_NEW_WITHOUT_OVERRIDE}. Для осознанного массового импорта нужен RED_ALLOW_MASS_CREATE=true."
        )

    snapshot = {
        "generated_at": now_iso(),
        "feed_url": effective_url,
        "offers": [item for item, _ in resolved],
    }

    if dry_run:
        for item, variant in resolved:
            if variant:
                if item["rrc_price"]:
                    report["price_updates"] += 1
                report["stock_updates"] += 2
        missing_active = [
            v for v in red_variants
            if s(v.get("id")) not in matched_variant_ids
            and s(v.get("status")).upper() != "ARCHIVED"
        ]
        report["missing_zeroed"] = len(missing_active)
        report["stock_updates"] += len(missing_active) * 2
        report["complete"] = not report["errors"]
        report["status"] = "ПРОВЕРКА УСПЕШНА" if report["complete"] else "ПРОВЕРКА С ОШИБКАМИ"
        report["finished_at"] = now_iso()
        write_report(report)
        write_snapshot(snapshot)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["complete"] else 1

    categories = kit.list_all("/v1/categories", {"status": "ACTIVE"}, "categories")

    def ensure_category(title, parent_id=""):
        rows = [
            row for row in categories
            if norm(row.get("title") or row.get("name")) == norm(title)
            and s(row.get("parent_id")) == s(parent_id)
        ]
        if rows:
            return s(rows[0].get("id"))
        created = kit.create_category(title, parent_id or None)
        cid = s(created.get("id"))
        if not cid:
            raise RuntimeError(f"KIT не вернул ID категории {title!r}")
        row = dict(created)
        row.setdefault("parent_id", parent_id)
        row.setdefault("title", title)
        categories.append(row)
        return cid

    root_category_id = ensure_category(ROOT_CATEGORY)
    source_category_to_kit = {}
    pending_categories = dict(source_categories)
    for _ in range(max(1, len(pending_categories) + 2)):
        progressed = False
        for cid, row in list(pending_categories.items()):
            parent_source = s(row.get("parent_id"))
            if parent_source and parent_source in source_categories and parent_source not in source_category_to_kit:
                continue
            parent_kit = source_category_to_kit.get(parent_source) or root_category_id
            source_category_to_kit[cid] = ensure_category(row.get("title") or cid, parent_kit)
            pending_categories.pop(cid, None)
            progressed = True
        if not pending_categories or not progressed:
            break
    for cid, row in list(pending_categories.items()):
        source_category_to_kit[cid] = ensure_category(row.get("title") or cid, root_category_id)

    characteristics = kit.list_all("/v1/characteristics", {"status": "ACTIVE"}, "characteristics")
    characteristic_cache = {}

    def ensure_characteristic(title):
        key = norm(title)
        if key in characteristic_cache:
            return characteristic_cache[key]
        rows = [x for x in characteristics if norm(x.get("title") or x.get("name")) == key]
        usable = [x for x in rows if s(x.get("type")).upper() in {"", "STRING"}]
        if usable:
            cid = s(usable[0].get("id"))
            characteristic_cache[key] = cid
            return cid
        created = kit.create_characteristic(title)
        cid = s(created.get("id"))
        if not cid:
            raise RuntimeError(f"KIT не вернул ID характеристики {title!r}")
        characteristics.append(created)
        characteristic_cache[key] = cid
        return cid

    def characteristics_payload(item):
        pairs = []

        def add(title, value):
            value = s(value)
            if not title or not value:
                return
            if any(norm(t) == norm(title) for t, _ in pairs):
                return
            pairs.append((title, value))

        add("Артикул", item["sku"])
        add("Код для сайта", item["sku"])
        add("Артикул поставщика", item["code"] or item["article"])
        for title, values in item["params"].items():
            add(title, " / ".join(values))

        payload = []
        for title, value in pairs:
            cid = ensure_characteristic(title)
            payload.append({
                "characteristic_id": cid,
                "value": value,
                "values": [value],
            })
        return payload

    def upload_media(item):
        media = []
        for url in item["pictures"]:
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
                    report["warnings"].append(f"{item['sku']}: фото {url}: {exc}")
        return media

    price_batch = []
    stock_batch = []

    for item, variant in resolved:
        if any(e.get("sku") == item["sku"] and e.get("stage") == "match" for e in report["errors"]):
            continue
        try:
            if variant:
                vid = s(variant.get("id"))
                if item["rrc_price"]:
                    price_batch.append({
                        "variant_id": vid,
                        "price": item["rrc_price"],
                        "manual_discount_price": item["rrc_price"],
                    })
                    report["price_updates"] += 1
                for wh in WAREHOUSE_NAMES:
                    stock_batch.append({
                        "variant_id": vid,
                        "warehouse_id": warehouse_ids[wh],
                        "quantity": int(item["stock"]),
                    })
                    report["stock_updates"] += 1
                continue

            if not item["rrc_price"]:
                raise RuntimeError("Для нового товара отсутствует <rrc_price>")

            category_id = source_category_to_kit.get(item["category_id"]) or root_category_id
            product = kit.create_product(category_id)
            product_id = s(product.get("id"))
            if not product_id:
                raise RuntimeError("KIT не вернул product_id")

            stocks = [
                {
                    "warehouse_id": warehouse_ids[wh],
                    "quantity": int(item["stock"]),
                    "reserved": 0,
                }
                for wh in WAREHOUSE_NAMES
            ]
            media = upload_media(item)
            body = {
                "sku": item["sku"],
                "name": item["name"],
                "description": item["description"],
                "status": "PUBLISHED",
                "product_id": product_id,
                "stocks": stocks,
                "pricing": {
                    "price": item["rrc_price"],
                    "manual_discount_price": item["rrc_price"],
                },
                "characteristics": characteristics_payload(item),
            }
            if media:
                body["media"] = media

            created = kit.create_variant(body)
            vid = s(created.get("id"))
            if not vid:
                raise RuntimeError("KIT не вернул variant_id")
            item["kit_variant_id"] = vid
            item["kit_sku"] = item["sku"]
            matched_variant_ids.add(vid)
            report["created_new"] += 1
            report["price_updates"] += 1
            report["stock_updates"] += 2

        except Exception as exc:
            report["errors"].append({
                "sku": item["sku"],
                "stage": "kit_update",
                "message": str(exc)[:1200],
            })

        if len(price_batch) >= 250:
            kit.update_prices(price_batch)
            price_batch.clear()
        if len(stock_batch) >= 500:
            kit.update_stocks(stock_batch)
            stock_batch.clear()

    if price_batch:
        kit.update_prices(price_batch)
    if stock_batch:
        kit.update_stocks(stock_batch)

    zero_batch = []
    for variant in red_variants:
        vid = s(variant.get("id"))
        if not vid or vid in matched_variant_ids:
            continue
        if s(variant.get("status")).upper() == "ARCHIVED":
            continue
        for wh in WAREHOUSE_NAMES:
            zero_batch.append({
                "variant_id": vid,
                "warehouse_id": warehouse_ids[wh],
                "quantity": 0,
            })
        report["missing_zeroed"] += 1

    if zero_batch:
        kit.update_stocks(zero_batch)
        report["stock_updates"] += len(zero_batch)

    report["complete"] = not report["errors"] and report["created_new"] == report["planned_new"]
    report["status"] = "УСПЕШНО" if report["complete"] else "ЗАВЕРШЕНО С ОШИБКАМИ"
    report["finished_at"] = now_iso()
    write_report(report)
    write_snapshot(snapshot)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["complete"] else 1


def main():
    parser = argparse.ArgumentParser(description="Red Black: полная выгрузка -> Yandex KIT")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        return run(dry_run=args.dry_run)
    except Exception as exc:
        report = {
            "started_at": now_iso(),
            "finished_at": now_iso(),
            "dry_run": bool(args.dry_run),
            "status": "ОШИБКА",
            "complete": False,
            "error": str(exc)[:3000],
        }
        write_report(report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
