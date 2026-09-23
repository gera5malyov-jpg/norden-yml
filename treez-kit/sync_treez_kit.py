#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import importlib.util
import json
import mimetypes
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE.parent / "aletan-kit" / "sync_aletan_kit.py"
REPORT_PATH = HERE / "last_sync_report.json"
STATE_PATH = HERE / "state.json"

SOURCE_XML_URL = "https://s3.q-parser.ru/automata/612af8f7ec414c/treez.ru.xml"
KIT_API = "https://api.kit.yandex.net"
BRAND = "TREEZ"
SUPPLIER = "treez"
ROOT_CATEGORY = "Treez"
WAREHOUSE_NAMES = ("МСК", "СПБ привозной")
STOCK_QTY = 100
MIN_INTERVAL_DAYS = 14


def load_base():
    spec = importlib.util.spec_from_file_location("treez_kit_base", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {BASE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


base = load_base()


def s(v):
    return str(v or "").strip()


def norm(v):
    return " ".join(s(v).casefold().replace("ё", "е").split())


def money(v):
    t = s(v).replace("\xa0", "").replace(" ", "").replace(",", ".")
    t = re.sub(r"[^0-9.\-]", "", t)
    if not t:
        return None
    try:
        d = Decimal(t)
    except (InvalidOperation, ValueError):
        return None
    return d if d >= 0 else None


def ruble(v):
    return Decimal(v).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def strip_markup(v):
    v = re.sub(r"<br\s*/?>", "\n", v or "", flags=re.I)
    v = re.sub(r"<[^>]+>", "", v)
    return html.unescape(v).strip()


def attr_value(attrs, name):
    m = re.search(rf"\b{name}\s*=\s*([\"'])(.*?)\1", attrs or "", flags=re.I | re.S)
    return html.unescape(m.group(2)).strip() if m else ""


def tag_text(block, tag):
    m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", block, flags=re.I | re.S)
    return strip_markup(m.group(1)) if m else ""


def tag_values(block, tag):
    return [
        strip_markup(x)
        for x in re.findall(rf"<{tag}\b[^>]*>(.*?)</{tag}>", block, flags=re.I | re.S)
        if strip_markup(x)
    ]


def parse_feed(raw):
    categories = {}
    for attrs, text in re.findall(r"<category\b([^>]*)>(.*?)</category>", raw, flags=re.I | re.S):
        cid = attr_value(attrs, "id")
        title = strip_markup(text)
        if cid and title:
            categories[cid] = title

    offers = {}
    duplicate_codes = {}
    for block in re.findall(r"<offer\b[^>]*>(.*?)</offer>", raw, flags=re.I | re.S):
        code = tag_text(block, "vendorcode") or tag_text(block, "model")
        if not code:
            continue

        params = []
        for attrs, value in re.findall(r"<param\b([^>]*)>(.*?)</param>", block, flags=re.I | re.S):
            val = strip_markup(value)
            if not val:
                continue
            title = attr_value(attrs, "name") or f"Параметр Treez {len(params)+1}"
            if norm(title) == "остаток":
                continue
            unit = attr_value(attrs, "unit")
            if unit and unit not in val:
                val = f"{val} {unit}"
            params.append((title, val))

        item = {
            "source_code": code,
            "model": tag_text(block, "model") or code,
            "name": tag_text(block, "name") or code,
            "price": money(tag_text(block, "price")),
            "category_id": tag_text(block, "categoryid"),
            "url": tag_text(block, "url"),
            "description": tag_text(block, "description"),
            "pictures": list(dict.fromkeys(tag_values(block, "picture"))),
            "params": params,
        }

        if code in offers:
            duplicate_codes.setdefault(code, [offers[code]]).append(item)
        else:
            offers[code] = item

    for code in duplicate_codes:
        offers.pop(code, None)

    return offers, categories, duplicate_codes


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
    raw = s(load_state().get("last_success_at"))
    if not raw:
        return True, None
    try:
        last = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return True, None
    age = (datetime.now(timezone.utc) - last.astimezone(timezone.utc)).total_seconds() / 86400
    return age >= MIN_INTERVAL_DAYS, age


def save_state_success():
    STATE_PATH.write_text(
        json.dumps({"last_success_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_report(report):
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


class Kit(base.KitClient):
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

    def patch_variant(self, variant_id, body):
        return self.request("PATCH", f"/v1/variants/{variant_id}", body=body, timeout=180)

    def update_stocks(self, items):
        for start in range(0, len(items), 5000):
            self.request(
                "POST",
                "/v1/variants/stocks/bulk_update",
                body={"items": items[start:start+5000]},
                timeout=180,
            )

    def upload_url(self, url, filename_prefix="treez"):
        delay = 0.55 - (time.monotonic() - self.last_request_at)
        if delay > 0:
            time.sleep(delay)
        self.last_request_at = time.monotonic()

        src = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0 Treez-KIT-Sync"})
        src.raise_for_status()
        if len(src.content) > 90 * 1024 * 1024:
            raise RuntimeError("Файл больше 90 МБ")

        content_type = (
            src.headers.get("Content-Type")
            or mimetypes.guess_type(urlparse(url).path)[0]
            or "application/octet-stream"
        )
        ext = os.path.splitext(urlparse(url).path)[1] or ".bin"
        endpoint = KIT_API + "/v1/files"
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        r = self.session.post(
            endpoint,
            headers=headers,
            files={"file": (filename_prefix + ext[:10], src.content, content_type)},
            timeout=180,
        )
        r.raise_for_status()
        return r.json() if r.content else {}


def exact_named(rows, title, parent_id=None):
    for row in rows:
        if norm(row.get("title")) != norm(title):
            continue
        if parent_id is not None and s(row.get("parent_id")) != s(parent_id):
            continue
        return row
    return None


def char_value(row):
    values = []
    if row.get("value") not in (None, ""):
        values.append(s(row.get("value")))
    values.extend(s(x) for x in (row.get("values") or []) if s(x))
    return values


def find_source_code(variant, titles):
    for row in variant.get("characteristics") or []:
        cid = s(row.get("characteristic_id"))
        title = norm(row.get("title") or titles.get(cid))
        if title in ("артикул поставщика", "код поставщика", "артикул treez"):
            vals = char_value(row)
            if vals:
                return vals[0]
    return ""


def gallery_images(item):
    out = []
    product_url = s(item.get("url")).split("#", 1)[0]
    if product_url:
        try:
            r = requests.get(
                product_url,
                timeout=60,
                headers={"User-Agent": "Mozilla/5.0 Treez-KIT-Sync"},
            )
            r.raise_for_status()
            page = html.unescape(r.text)

            full = re.findall(
                r"""(?:https?://[^"'<>\s]+)?/photos/resize/1600_1800/[^"'<>\s]+""",
                page,
                flags=re.I,
            )
            full = [urljoin(product_url, x) for x in full]

            for url in full:
                url = url.split("?", 1)[0]
                if url not in out:
                    out.append(url)
        except Exception:
            pass

    for url in item.get("pictures") or []:
        url = s(url)
        if url and url not in out:
            out.append(url)

    return out[:30]


def build_media(kit, item, report):
    media = []
    gallery = gallery_images(item)
    report["gallery_images_found"] = report.get("gallery_images_found", 0) + len(gallery)
    for url in gallery:
        try:
            uploaded = kit.upload_url(url, "treez-image")
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
            if len(report["warnings"]) < 200:
                report["warnings"].append({
                    "source_code": item["source_code"],
                    "stage": "image",
                    "url": url,
                    "message": str(exc)[:500],
                })
    return media


def find_video_urls(product_url):
    if not product_url:
        return []
    try:
        r = requests.get(product_url.split("#", 1)[0], timeout=45, headers={"User-Agent": "Mozilla/5.0 Treez-KIT-Sync"})
        r.raise_for_status()
    except Exception:
        return []

    text = html.unescape(r.text)
    patterns = [
        r"""https?://[^"' <>()]+\.mp4(?:\?[^"' <>()]*)?""",
        r"""(?:"|')(/upload/files/VIDEO/[^"']+\.mp4(?:\?[^"']*)?)(?:"|')""",
    ]
    out = []
    for pattern in patterns:
        for m in re.findall(pattern, text, flags=re.I):
            url = m if isinstance(m, str) else m[0]
            url = urljoin(product_url, url)
            if url not in out:
                out.append(url)
    return out[:2]


def try_add_videos(kit, variant_id, existing_media, item, report):
    urls = find_video_urls(item["url"])
    if not urls:
        return
    media = list(existing_media or [])
    for url in urls:
        try:
            uploaded = kit.upload_url(url, "treez-video")
            file_id = s(uploaded.get("id"))
            if not file_id:
                continue
            candidate = media + [{
                "type": "VIDEO",
                "display_sequence": len(media),
                "video_id": file_id,
            }]
            kit.patch_variant(variant_id, {"media": candidate})
            media = candidate
            report["videos_uploaded"] += 1
        except Exception as exc:
            report["video_errors"] += 1
            if len(report["warnings"]) < 200:
                report["warnings"].append({
                    "source_code": item["source_code"],
                    "stage": "video",
                    "url": url,
                    "message": str(exc)[:500],
                })
            break


def main_run(dry_run=False, force=False, skip_video=False):
    report = {
        "status": "running",
        "dry_run": bool(dry_run),
        "source": SOURCE_XML_URL,
        "supplier": SUPPLIER,
        "brand": BRAND,
        "interval_days": MIN_INTERVAL_DAYS,
        "sku_rule": "TREEZ-<код KIT>",
        "customer_price_rule": "цена Treez × 0.97",
        "old_price_rule": "цена Treez",
        "stock_rule": {name: STOCK_QTY for name in WAREHOUSE_NAMES},
        "missing_from_source_rule": "остаток 0",
        "source_offers": 0,
        "created": 0,
        "updated_existing": 0,
        "existing_media_repaired": 0,
        "price_updates": 0,
        "stock_updates": 0,
        "zeroed_missing": 0,
        "images_uploaded": 0,
        "gallery_images_found": 0,
        "image_errors": 0,
        "videos_uploaded": 0,
        "video_errors": 0,
        "duplicate_source_codes": [],
        "errors": [],
        "warnings": [],
    }

    if not dry_run:
        due, age = due_for_live(force)
        report["days_since_last_success"] = None if age is None else round(age, 2)
        if not due:
            report["status"] = "skipped"
            report["reason"] = "С последнего успешного обновления прошло менее 14 дней"
            write_report(report)
            return 0

    raw = requests.get(
        SOURCE_XML_URL,
        timeout=180,
        headers={"User-Agent": "Mozilla/5.0 Treez-GitHub-Parser"},
    )
    raw.raise_for_status()
    text = raw.content.decode("utf-8-sig", errors="replace")
    offers, category_map, duplicates = parse_feed(text)
    report["source_offers"] = len(offers)
    report["source_categories"] = len(category_map)
    report["duplicate_source_codes"] = sorted(duplicates)[:100]
    if len(offers) < 100:
        raise RuntimeError(f"Подозрительно мало товаров в источнике: {len(offers)}")

    token = s(os.getenv("YANDEX_KIT_TOKEN"))
    if not token:
        raise RuntimeError("YANDEX_KIT_TOKEN is not configured")
    kit = Kit(token)

    warehouses = kit.list_all("/v1/warehouses", {"status": "ACTIVE"}, "warehouses")
    warehouse_ids = {s(x.get("title")): s(x.get("id")) for x in warehouses if s(x.get("id"))}
    missing_wh = [x for x in WAREHOUSE_NAMES if not warehouse_ids.get(x)]
    if missing_wh:
        raise RuntimeError("Не найдены склады KIT: " + ", ".join(missing_wh))
    report["warehouses"] = {x: warehouse_ids[x] for x in WAREHOUSE_NAMES}

    characteristics = kit.list_all("/v1/characteristics", {"status": "ACTIVE"}, "characteristics")
    char_by_title = {norm(x.get("title")): x for x in characteristics if s(x.get("id"))}

    def ensure_char(title):
        row = char_by_title.get(norm(title))
        if row:
            return s(row.get("id"))
        if dry_run:
            return "DRY-" + re.sub(r"\W+", "-", title)[:50]
        row = kit.create_characteristic(title)
        cid = s(row.get("id"))
        if not cid:
            raise RuntimeError(f"Не удалось создать характеристику {title}")
        char_by_title[norm(title)] = row
        characteristics.append(row)
        return cid

    source_code_char = ensure_char("Артикул поставщика")
    model_char = ensure_char("Модель Treez")
    source_category_char = ensure_char("Категория Treez")
    param_char_ids = {}
    for item in offers.values():
        for title, _ in item["params"]:
            if norm(title) == "остаток":
                continue
            if title not in param_char_ids:
                param_char_ids[title] = ensure_char(title)

    categories = kit.list_all("/v1/categories", {"status": "ACTIVE"}, "categories")
    root = exact_named(categories, ROOT_CATEGORY, parent_id="")
    if root:
        root_id = s(root.get("id"))
    elif dry_run:
        root_id = "DRY_ROOT"
    else:
        root = kit.create_category(ROOT_CATEGORY)
        root_id = s(root.get("id"))
        categories.append(dict(root, parent_id=""))
    if not root_id:
        raise RuntimeError("Не удалось определить категорию Treez")

    category_ids = {}
    def kit_category(source_cid):
        title = category_map.get(source_cid) or ""
        if not title:
            return root_id
        key = (source_cid, title)
        if key in category_ids:
            return category_ids[key]
        row = exact_named(categories, title, parent_id=root_id)
        if row:
            cid = s(row.get("id"))
        elif dry_run:
            cid = "DRY_CAT_" + re.sub(r"\W+", "_", source_cid or title)
        else:
            row = kit.create_category(title, root_id)
            cid = s(row.get("id"))
            categories.append(dict(row, parent_id=root_id))
        category_ids[key] = cid or root_id
        return category_ids[key]

    titles = {s(x.get("id")): s(x.get("title")) for x in characteristics if s(x.get("id"))}
    existing_rows = kit.list_all("/v1/variants", {"name": BRAND}, "variants")
    existing = []
    by_source = {}
    for row in existing_rows:
        if norm(row.get("brand")) != norm(BRAND) or s(row.get("status")).upper() == "ARCHIVED":
            continue
        variant = row
        code = find_source_code(variant, titles)
        if not code and not (variant.get("characteristics") or []):
            try:
                variant = kit.get_variant(s(row.get("id")))
                code = find_source_code(variant, titles)
            except Exception:
                pass
        if code:
            existing.append(variant)
            if code not in by_source:
                by_source[code] = variant
            else:
                report["warnings"].append({
                    "source_code": code,
                    "stage": "existing_index",
                    "message": "В KIT найдено более одной активной карточки TREEZ с одним артикулом поставщика",
                })

    price_batch = []
    stock_batch = []

    for idx, (code, item) in enumerate(offers.items(), start=1):
        source_price = item["price"]
        old_price = ruble(source_price) if source_price is not None else None
        customer_price = ruble(source_price * Decimal("0.97")) if source_price is not None else None

        char_rows = [
            {"characteristic_id": source_code_char, "value": code},
            {"characteristic_id": model_char, "value": item["model"]},
            {"characteristic_id": source_category_char, "value": category_map.get(item["category_id"]) or item["category_id"]},
        ]
        for title, value in item["params"]:
            if norm(title) == "остаток":
                continue
            cid = param_char_ids.get(title)
            if cid and value:
                char_rows.append({"characteristic_id": cid, "value": value})

        description = item["description"]
        if item["params"]:
            extra = "\n".join(
                f"{title}: {value}"
                for title, value in item["params"]
                if norm(title) != "остаток"
            )
            if extra:
                description = (description + "\n\n" + extra).strip()
        description = re.sub(r"(?im)^\s*Остаток\s*:\s*0(?:[.,]0*)?\s*$", "", description)
        description = re.sub(r"\n{3,}", "\n\n", description).strip()

        variant = by_source.get(code)
        if variant:
            vid = s(variant.get("id"))
            if not dry_run:
                try:
                    detail = variant if "media" in variant else kit.get_variant(vid)
                    patch_body = {
                        "name": item["name"],
                        "description": description,
                        "brand": BRAND,
                        "characteristics": char_rows,
                    }

                    gallery = gallery_images(item)
                    current_media = list(detail.get("media") or [])
                    current_image_count = sum(
                        1 for m in current_media
                        if s(m.get("type")).upper() == "IMAGE"
                    )
                    if len(gallery) > current_image_count:
                        fresh_images = build_media(kit, dict(item, pictures=gallery), report)
                        preserved = [
                            m for m in current_media
                            if s(m.get("type")).upper() != "IMAGE"
                        ]
                        patch_body["media"] = fresh_images + preserved
                        report["existing_media_repaired"] = report.get("existing_media_repaired", 0) + 1

                    kit.patch_variant(vid, patch_body)
                except Exception as exc:
                    report["errors"].append({"source_code": code, "stage": "update_content", "message": str(exc)[:500]})
                    continue
            report["updated_existing"] += 1
        else:
            if dry_run:
                report["created"] += 1
                continue

            product = kit.create_product(kit_category(item["category_id"]))
            product_id = s(product.get("id"))
            if not product_id:
                report["errors"].append({"source_code": code, "stage": "create_product", "message": "KIT не вернул product_id"})
                continue

            media = build_media(kit, item, report)
            tmp_sku = "TREEZ-TMP-" + re.sub(r"[^0-9A-Za-zА-Яа-я._/-]+", "-", code)[:80]
            body = {
                "sku": tmp_sku,
                "name": item["name"],
                "description": description,
                "status": "PUBLISHED",
                "product_id": product_id,
                "brand": BRAND,
                "stocks": [
                    {"warehouse_id": warehouse_ids[wh], "quantity": STOCK_QTY, "reserved": 0}
                    for wh in WAREHOUSE_NAMES
                ],
                "characteristics": char_rows,
            }
            if old_price is not None and customer_price is not None:
                body["pricing"] = {
                    "price": str(old_price),
                    "manual_discount_price": str(customer_price),
                }
            if media:
                body["media"] = media

            try:
                created = kit.create_variant(body)
                vid = s(created.get("id"))
                if not vid:
                    raise RuntimeError("KIT не вернул variant_id")
                detail = created if created.get("kit_id") else kit.get_variant(vid)
                kit_code = s(detail.get("kit_id"))
                if kit_code:
                    try:
                        kit.patch_variant(vid, {"sku": f"TREEZ-{kit_code}"})
                    except Exception as exc:
                        report["warnings"].append({
                            "source_code": code,
                            "stage": "final_sku",
                            "message": str(exc)[:500],
                        })
                if not skip_video:
                    try_add_videos(kit, vid, media, item, report)
                report["created"] += 1
                by_source[code] = dict(detail, id=vid, characteristics=char_rows)
            except Exception as exc:
                report["errors"].append({"source_code": code, "stage": "create_variant", "message": str(exc)[:500]})
                continue

        if not dry_run:
            if old_price is not None and customer_price is not None:
                price_batch.append({
                    "variant_id": vid,
                    "price": str(old_price),
                    "manual_discount_price": str(customer_price),
                })
                report["price_updates"] += 1
            for wh in WAREHOUSE_NAMES:
                stock_batch.append({
                    "variant_id": vid,
                    "warehouse_id": warehouse_ids[wh],
                    "quantity": STOCK_QTY,
                })
                report["stock_updates"] += 1

        if idx % 100 == 0:
            print(f"Treez: обработано {idx}/{len(offers)}", flush=True)

    source_codes = set(offers)
    for variant in existing:
        code = find_source_code(variant, titles)
        if code and code not in source_codes:
            vid = s(variant.get("id"))
            for wh in WAREHOUSE_NAMES:
                stock_batch.append({
                    "variant_id": vid,
                    "warehouse_id": warehouse_ids[wh],
                    "quantity": 0,
                })
                report["stock_updates"] += 1
            report["zeroed_missing"] += 1

    if not dry_run:
        if price_batch:
            kit.update_prices(price_batch)
        if stock_batch:
            kit.update_stocks(stock_batch)
        if report["errors"]:
            report["status"] = "partial"
            report["complete"] = False
        else:
            report["status"] = "ok"
            report["complete"] = True
            save_state_success()
    else:
        report["status"] = "dry_run"
        report["complete"] = not report["errors"]

    write_report(report)
    return 0 if not report["errors"] else 2


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--skip-video", action="store_true")
    args = p.parse_args()
    try:
        return main_run(args.dry_run, args.force, args.skip_video)
    except Exception as exc:
        write_report({
            "status": "error",
            "complete": False,
            "dry_run": bool(args.dry_run),
            "error": str(exc)[:3000],
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

# trigger: initial Treez live sync
