from __future__ import annotations

import argparse
import html
import json
import os
import re
import tempfile
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from urllib.parse import urljoin, urlparse

from samson_kit.http import SafeSession
from samson_kit.kit_client import KitClient
from samson_kit.rules import calculate_prices

KOMUS_BASE = "https://komus-opt.ru/api2"
KOMUS_SITE = "https://komus-opt.ru"
SKU_PREFIX = "kom-"
TARGET_WAREHOUSE = "СПБ"
TARGET_REGION_NAMES = {"Москва", "Санкт-Петербург"}


def clean_html(value):
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def norm(value):
    return str(value or "").strip().casefold()


def money_int(value):
    try:
        return Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_CEILING)
    except Exception:
        return None


class KomusClient:
    def __init__(self, token, http):
        self.token = str(token or "").strip()
        if not self.token:
            raise ValueError("empty Komus token")
        self.http = http

    @property
    def headers(self):
        return {
            "token": self.token,
            "Accept": "application/json",
            "User-Agent": "Megapolis-Komus-KIT/1.0",
        }

    def get(self, path, params=None):
        return self.http.request_json(
            "GET", KOMUS_BASE + path,
            params={"format": "json", **(params or {})},
            headers=self.headers,
        )

    def post(self, path, artnumbers, params=None):
        return self.http.request_json(
            "POST", KOMUS_BASE + path,
            params={"format": "json", **(params or {})},
            headers={**self.headers, "Content-Type": "application/json"},
            json_body={"artnumbers": [int(x) for x in artnumbers]},
        )

    def regions(self):
        payload = self.get("/shipdata/")
        out = {}
        for row in payload.get("content") or []:
            info = (row or {}).get("shipmentInfo") or {}
            rid = info.get("regionID")
            name = str(info.get("regionName") or "").strip()
            if rid is not None and name:
                out[int(rid)] = name
        return out

    def iter_elements(self, limit=1000):
        page = 1
        while True:
            payload = self.get("/elements", {"page": page, "limit": limit})
            rows = payload.get("content") or []
            if not isinstance(rows, list):
                raise RuntimeError("invalid Komus /elements response")
            for row in rows:
                if isinstance(row, dict):
                    yield row
            nxt = payload.get("next")
            if not nxt:
                break
            page = int(nxt)

    def categories(self, region_id):
        rows = []
        page = 1
        while True:
            payload = self.get("/categories", {"page": page, "limit": 1000, "region": int(region_id)})
            batch = payload.get("content") or []
            if not isinstance(batch, list):
                raise RuntimeError("invalid Komus /categories response")
            rows.extend(x for x in batch if isinstance(x, dict))
            nxt = payload.get("next")
            if not nxt:
                break
            page = int(nxt)
        return rows

    def stocks(self, artnumbers):
        return self.post("/stock/", artnumbers)

    def prices(self, artnumbers):
        return self.post("/prices/", artnumbers)

    def props(self, artnumbers):
        return self.post(
            "/props/", artnumbers,
            {"fields": "parentId,description,baseprops,barcodes,images,specifications"},
        )


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def index_by_art(payload, content_key="content"):
    out = {}
    for row in payload.get(content_key) or []:
        if isinstance(row, dict) and row.get("artnumber") is not None:
            out[str(row["artnumber"]).strip()] = row
    return out


def build_kit_index(kit):
    buckets = {}
    for row in kit.iter_variants({"name": SKU_PREFIX}):
        sku = str(row.get("sku") or "").strip()
        if not sku.lower().startswith(SKU_PREFIX):
            continue
        buckets.setdefault(sku.lower(), []).append(row)
    unique = {k: v[0] for k, v in buckets.items() if len(v) == 1}
    dup = {k: v for k, v in buckets.items() if len(v) > 1}
    return unique, dup


def current_stock(variant, warehouse_id):
    for row in variant.get("stocks") or []:
        if str(row.get("warehouse_id") or "") == str(warehouse_id):
            try:
                return int(row.get("quantity") or 0)
            except Exception:
                return None
    return 0


class Runner:
    def __init__(self, komus, kit, http, *, dry_run=False, skip_items=0, max_items=None, max_new=None, new_only=False):
        self.komus = komus
        self.kit = kit
        self.http = http
        self.dry_run = bool(dry_run)
        self.skip_items = max(0, int(skip_items or 0))
        self.max_items = int(max_items) if max_items not in (None, "", 0, "0") else None
        self.max_new = int(max_new) if max_new not in (None, "", 0, "0") else None
        self.new_only = bool(new_only)
        self.kit_categories = []
        self.kit_characteristics = []
        self.report = {
            "status": "running",
            "dry_run": self.dry_run,
            "source_complete": False,
            "source_rows_seen": 0,
            "eligible_in_stock": 0,
            "new_products_created": 0,
            "price_changes": 0,
            "stock_changes": 0,
            "absent_to_zero": 0,
            "duplicate_kit_skus": 0,
            "error_count": 0,
            "errors": [],
            "warning_count": 0,
            "warnings": [],
            "regions": {},
            "target_region_ids": [],
            "skip_items": self.skip_items,
            "max_items": self.max_items,
            "max_new": self.max_new,
            "new_only": self.new_only,
        }

    def error(self, sku, exc):
        self.report["error_count"] += 1
        if len(self.report["errors"]) < 200:
            self.report["errors"].append({"sku": sku, "message": str(exc)[:500]})

    def warn(self, message):
        self.report["warning_count"] += 1
        if len(self.report["warnings"]) < 200:
            self.report["warnings"].append(str(message)[:500])

    def ensure_category(self, category_id, source_categories):
        chain = []
        seen = set()
        cid = str(category_id or "").strip()
        while cid and cid not in seen:
            seen.add(cid)
            row = source_categories.get(cid)
            if not row:
                break
            chain.append(row)
            parent = row.get("parentId")
            cid = "" if parent in (None, "", 0, "0") else str(parent)
        chain.reverse()
        if not chain:
            raise RuntimeError(f"missing Komus category {category_id}")

        parent = ""
        for src in chain:
            title = str(src.get("name") or "").strip()
            if not title:
                raise RuntimeError("Komus category without name")
            matches = [
                x for x in self.kit_categories
                if norm(x.get("title")) == norm(title)
                and str(x.get("parent_id") or "") == parent
            ]
            if len(matches) > 1:
                raise RuntimeError(f"ambiguous KIT category {title!r}")
            if matches:
                kid = str(matches[0].get("id") or "").strip()
            elif self.dry_run:
                kid = "dry-komus-cat-" + str(src.get("id") or len(self.kit_categories) + 1)
                self.kit_categories.append({"id": kid, "title": title, "parent_id": parent})
            else:
                created = self.kit.create_category(title, parent or None)
                kid = str(created.get("id") or "").strip()
                if not kid:
                    raise RuntimeError(f"KIT category create failed: {title}")
                self.kit_categories.append(created)
            parent = kid
        return parent

    def ensure_characteristics(self, prop):
        out = []
        specs = prop.get("specifications") or []
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            title = str(spec.get("name") or "").strip()
            value = spec.get("value")
            if not title or value in (None, ""):
                continue
            value = str(value).strip()
            if not value:
                continue
            matches = [
                x for x in self.kit_characteristics
                if norm(x.get("title")) == norm(title)
                and str(x.get("type") or "").upper() == "STRING"
            ]
            if len(matches) > 1:
                self.warn(f"ambiguous KIT characteristic skipped: {title}")
                continue
            if matches:
                cid = str(matches[0].get("id") or "").strip()
            elif self.dry_run:
                cid = "dry-komus-char-" + str(len(self.kit_characteristics) + 1)
                self.kit_characteristics.append({"id": cid, "title": title, "type": "STRING"})
            else:
                created = self.kit.create_characteristic(title, "STRING", "SINGLE")
                cid = str(created.get("id") or "").strip()
                if not cid:
                    self.warn(f"KIT characteristic create failed: {title}")
                    continue
                self.kit_characteristics.append(created)
            out.append({"characteristic_id": cid, "value": value, "values": [value]})
        return out

    def prepare_media(self, prop):
        if self.dry_run:
            return []
        media = []
        images = prop.get("images") or []
        for raw_url in images[:20]:
            url = urljoin(KOMUS_SITE, str(raw_url))
            try:
                suffix = Path(urlparse(url).path).suffix or ".jpg"
                with tempfile.TemporaryDirectory(prefix="komus-img-") as td:
                    path = os.path.join(td, "image" + suffix[:10])
                    self.http.download_to_file(url, path)
                    uploaded = self.kit.upload_image(path)
                    file_id = str(uploaded.get("id") or "").strip()
                    if not file_id:
                        raise RuntimeError("KIT did not return image file id")
                    media.append({"type": "IMAGE", "display_sequence": len(media), "image_id": file_id})
            except Exception as exc:
                self.warn(f"image failed: {url}: {exc}")
        return media

    def run(self):
        warehouse_id = self.kit.resolve_warehouse_exact(TARGET_WAREHOUSE)
        regions = self.komus.regions()
        self.report["regions"] = {str(k): v for k, v in regions.items()}
        target_region_ids = sorted(rid for rid, name in regions.items() if name in TARGET_REGION_NAMES)
        if not target_region_ids:
            raise RuntimeError(f"Komus token has no target regions {sorted(TARGET_REGION_NAMES)}")
        self.report["target_region_ids"] = target_region_ids

        # Categories are requested from all target regions and merged by id.
        source_categories = {}
        for rid in target_region_ids:
            for row in self.komus.categories(rid):
                if row.get("id") is not None:
                    source_categories[str(row["id"])] = row

        kit_index, duplicates = build_kit_index(self.kit)
        self.report["duplicate_kit_skus"] = len(duplicates)
        self.kit_categories = self.kit.list_categories()
        self.kit_characteristics = self.kit.list_characteristics()

        source_rows = list(self.komus.iter_elements())
        all_arts = [str(x.get("artnumber") or "").strip() for x in source_rows if str(x.get("artnumber") or "").strip()]
        price_map = {}
        stock_map = {}
        prop_map = {}

        # Pull dynamic data in Komus-supported batches (<=1000).
        for arts in chunked(all_arts, 1000):
            try:
                price_map.update(index_by_art(self.komus.prices(arts)))
            except Exception as exc:
                self.warn(f"prices batch unavailable: {exc}")
            try:
                stock_map.update(index_by_art(self.komus.stocks(arts)))
            except Exception as exc:
                self.warn(f"stocks batch unavailable: {exc}")

        seen_eligible = set()
        complete = True
        processed_after_skip = 0
        price_batch = []
        stock_batch = []

        for source_pos, base in enumerate(source_rows):
            if source_pos < self.skip_items:
                continue
            if self.max_items is not None and processed_after_skip >= self.max_items:
                complete = False
                break
            processed_after_skip += 1
            self.report["source_rows_seen"] += 1

            art = str(base.get("artnumber") or "").strip()
            if not art:
                continue
            sku = SKU_PREFIX + art
            key = sku.lower()

            stock_row = stock_map.get(art) or {}
            quantities = []
            for st in stock_row.get("stock") or []:
                try:
                    rid = int(st.get("region"))
                    qty = int(st.get("quantity") or 0)
                except Exception:
                    continue
                if rid in target_region_ids:
                    quantities.append(max(0, qty))
            desired_stock = sum(quantities)
            if desired_stock <= 0:
                continue

            seen_eligible.add(key)
            self.report["eligible_in_stock"] += 1
            if key in duplicates:
                continue

            variant = kit_index.get(key)
            price_row = price_map.get(art) or {}
            partner_price = None
            rows = price_row.get("prices") or []
            preferred = [x for x in rows if str(x.get("region")) == "0"] or rows
            if preferred:
                partner_price = preferred[0].get("partnerPrice")
            prices = calculate_prices(partner_price)

            if variant:
                if self.new_only:
                    continue
                if prices:
                    pricing = variant.get("pricing") or {}
                    if money_int(pricing.get("price")) != money_int(prices.old) or money_int(pricing.get("manual_discount_price")) != money_int(prices.sale):
                        self.report["price_changes"] += 1
                        vid = str(variant.get("id") or "").strip()
                        if vid:
                            price_batch.append({
                                "variant_id": vid,
                                "price": f"{prices.old:.2f}",
                                "manual_discount_price": f"{prices.sale:.2f}",
                            })
                            if len(price_batch) >= 500 and not self.dry_run:
                                self.kit.bulk_update_prices(price_batch)
                                price_batch.clear()
                if current_stock(variant, warehouse_id) != desired_stock:
                    self.report["stock_changes"] += 1
                    vid = str(variant.get("id") or "").strip()
                    if vid:
                        stock_batch.append({
                            "variant_id": vid,
                            "warehouse_id": str(warehouse_id),
                            "quantity": int(desired_stock),
                        })
                        if len(stock_batch) >= 500 and not self.dry_run:
                            self.kit.bulk_update_stocks(stock_batch)
                            stock_batch.clear()
                continue

            try:
                prop = prop_map.get(art)
                if prop is None:
                    payload = self.komus.props([art])
                    prop = index_by_art(payload).get(art) or {}
                    prop_map[art] = prop

                category_id = prop.get("parentId") or base.get("categoryId")
                kit_category_id = self.ensure_category(category_id, source_categories)
                chars = self.ensure_characteristics(prop)
                media = self.prepare_media(prop)
                brand = str((prop.get("baseprops") or {}).get("trademark") or "").strip()
                description = clean_html(prop.get("description"))

                payload = {
                    "sku": sku,
                    "name": str(base.get("name") or f"Komus {art}").strip(),
                    "description": description,
                    "status": "PUBLISHED",
                    "brand": brand or "Комус",
                    "characteristics": chars,
                    "stocks": [{
                        "warehouse_id": str(warehouse_id),
                        "quantity": int(desired_stock),
                        "reserved": 0,
                    }],
                }
                if media:
                    payload["media"] = media
                if prices:
                    payload["pricing"] = {
                        "price": f"{prices.old:.2f}",
                        "manual_discount_price": f"{prices.sale:.2f}",
                    }

                if self.dry_run:
                    self.report["new_products_created"] += 1
                else:
                    product = self.kit.create_product(kit_category_id)
                    product_id = str(product.get("id") or "").strip()
                    if not product_id:
                        raise RuntimeError("KIT did not return product id")
                    payload["product_id"] = product_id
                    created = self.kit.create_variant(payload)
                    variant_id = str(created.get("id") or "").strip()
                    if not variant_id:
                        raise RuntimeError("KIT did not return variant id")
                    self.report["new_products_created"] += 1
                    normalized = dict(payload)
                    normalized.update(created)
                    kit_index[key] = normalized
                if self.max_new is not None and self.report["new_products_created"] >= self.max_new:
                    complete = False
                    break
            except Exception as exc:
                self.error(sku, exc)

        if price_batch and not self.dry_run:
            self.kit.bulk_update_prices(price_batch)
        if stock_batch and not self.dry_run:
            self.kit.bulk_update_stocks(stock_batch)

        self.report["source_complete"] = bool(complete and self.skip_items == 0 and self.max_items is None and self.max_new is None)

        # Only a proven full daily scan is allowed to zero disappeared Komus cards.
        if self.report["source_complete"] and not self.new_only:
            zero_updates = []
            for key, variant in kit_index.items():
                if key in seen_eligible or key in duplicates:
                    continue
                if current_stock(variant, warehouse_id) == 0:
                    continue
                vid = str(variant.get("id") or "").strip()
                if vid:
                    zero_updates.append({
                        "variant_id": vid,
                        "warehouse_id": str(warehouse_id),
                        "quantity": 0,
                    })
            self.report["absent_to_zero"] = len(zero_updates)
            if zero_updates and not self.dry_run:
                self.kit.bulk_update_stocks(zero_updates)

        self.report["status"] = "ok" if not self.report["errors"] else ("degraded" if self.report["source_rows_seen"] else "failed")
        return self.report


def pick_token():
    for name in ("KOMUS_API", "KOMUS_API_KEY", "KOMUS_TOKEN", "KOMUS_API_TOKEN"):
        value = os.getenv(name)
        if value and value.strip():
            return name, value.strip()
    raise RuntimeError("Komus API secret not found")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-items", type=int, default=0)
    p.add_argument("--max-items", type=int, default=None)
    p.add_argument("--max-new", type=int, default=None)
    p.add_argument("--new-only", action="store_true")
    p.add_argument("--report", default="komus-kit/last_sync_report.json")
    args = p.parse_args()

    secret_name, token = pick_token()
    http = SafeSession(max_attempts=8, timeout=(15, 120))
    komus = KomusClient(token, http)
    kit = KitClient(os.environ["YANDEX_KIT_TOKEN"], http, min_request_interval=0.35)

    try:
        report = Runner(
            komus, kit, http,
            dry_run=args.dry_run,
            skip_items=args.skip_items,
            max_items=args.max_items,
            max_new=args.max_new,
            new_only=args.new_only,
        ).run()
        report["secret_name"] = secret_name
    except Exception as exc:
        report = {
            "status": "failed",
            "dry_run": args.dry_run,
            "error_count": 1,
            "errors": [{"sku": "BOOTSTRAP", "message": str(exc)[:1000]}],
            "secret_name": secret_name,
        }

    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(1 if report.get("status") == "failed" else 0)


if __name__ == "__main__":
    main()
