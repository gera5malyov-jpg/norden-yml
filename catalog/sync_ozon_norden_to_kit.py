#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "catalog" / "ozon_norden_to_kit_report.json"
TRIGGER = ROOT / "catalog" / "ozon_norden_to_kit_trigger.txt"
OZON_BASE = "https://api-seller.ozon.ru"
BRAND_ATTR_ID = 85
DESCRIPTION_ATTR_ID = 4191
BRAND = "Norden"
ARTICLE_TITLE = "Артикул"


def load(path, name):
    p = ROOT / path
    spec = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


BRIDGE = load(Path("norden-kit") / "webasyst_n100_to_kit_once.py", "kit_bridge")
POST = load(Path("catalog") / "sync_ozon_readback_to_kit.py", "ozon_postsync")
MOD = load(Path("norden-kit") / "sync_norden_kit.py", "norden_sync")


def s(v):
    return str(v or "").strip()


def norm(v):
    return re.sub(r"\s+", " ", s(v)).casefold().strip()


def dec(v):
    try:
        if v is None or s(v) == "":
            return None
        return Decimal(s(v).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i+n]


class Ozon:
    def __init__(self):
        self.session = requests.Session()
        self.headers = {
            "Client-Id": os.environ["OZON_CLIENT_ID"],
            "Api-Key": os.environ["OZON_API_KEY"],
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def post(self, path, body, attempts=8):
        last = None
        for attempt in range(attempts):
            try:
                r = self.session.post(OZON_BASE + path, headers=self.headers, json=body, timeout=120)
                if r.status_code == 429 or r.status_code >= 500:
                    if attempt + 1 < attempts:
                        time.sleep(min(30, 2 ** attempt))
                        continue
                if not r.ok:
                    raise RuntimeError(f"{path}: HTTP {r.status_code}: {r.text[:1200]}")
                return r.json() if r.content else {}
            except Exception as exc:
                last = exc
                if attempt + 1 < attempts:
                    time.sleep(min(30, 2 ** attempt))
                    continue
                raise
        raise last

    def list_visibility(self, visibility):
        out, last_id, seen = [], "", set()
        while True:
            body = {"filter": {"visibility": visibility}, "limit": 1000}
            if last_id:
                body["last_id"] = last_id
            d = self.post("/v3/product/list", body)
            r = d.get("result") or {}
            items = r.get("items") or []
            out.extend(items)
            nxt = s(r.get("last_id"))
            total = int(r.get("total") or 0)
            if not items or (total and len(out) >= total) or not nxt or nxt == last_id or nxt in seen:
                break
            seen.add(last_id)
            last_id = nxt
        return out

    def attributes(self, ids):
        out = []
        for batch in chunks(ids, 1000):
            d = self.post("/v4/product/info/attributes", {
                "filter": {"product_id": batch, "visibility": "ALL"},
                "limit": 1000,
            })
            out.extend(d.get("result") or [])
        return out

    def info(self, ids):
        out = []
        for batch in chunks(ids, 1000):
            d = self.post("/v3/product/info/list", {"product_id": batch})
            out.extend(d.get("items") or (d.get("result") or {}).get("items") or [])
        return out

    def prices(self, ids):
        out = []
        for batch in chunks(ids, 100):
            cursor = ""
            while True:
                body = {"filter": {"product_id": batch, "visibility": "ALL"}, "limit": 100}
                if cursor:
                    body["cursor"] = cursor
                d = self.post("/v5/product/info/prices", body)
                items = d.get("items") or []
                out.extend(items)
                nxt = s(d.get("cursor"))
                if not items or not nxt or nxt == cursor:
                    break
                cursor = nxt
        return out

    def category_tree(self):
        return self.post("/v1/description-category/tree", {"language": "DEFAULT"})

    def schema(self, description_category_id, type_id):
        d = self.post("/v1/description-category/attribute", {
            "description_category_id": int(description_category_id),
            "type_id": int(type_id),
            "language": "DEFAULT",
        })
        return {int(x.get("id") or 0): x for x in (d.get("result") or []) if int(x.get("id") or 0)}


def attr_values(card, aid):
    for a in card.get("attributes") or []:
        if int(a.get("id") or a.get("attribute_id") or 0) == aid:
            return [s(v.get("value")) for v in (a.get("values") or []) if s(v.get("value"))]
    return []


def is_norden(card):
    return any(x.upper() == "NORDEN" for x in attr_values(card, BRAND_ATTR_ID))


def description_from_ozon(card):
    vals = attr_values(card, DESCRIPTION_ATTR_ID)
    return vals[0] if vals else ""


def image_urls(card):
    vals = []
    p = s(card.get("primary_image"))
    if p:
        vals.append(p)
    for x in card.get("images") or []:
        if isinstance(x, dict):
            u = s(x.get("url") or x.get("file_name") or x.get("image"))
        else:
            u = s(x)
        if u and u not in vals:
            vals.append(u)
    return vals[:20]


def price_patch(price_row):
    p = price_row.get("price") or {}
    sale = dec(p.get("price"))
    old = dec(p.get("old_price"))
    if sale is None or sale <= 0:
        return None
    if old is not None and old > sale:
        return {"price": str(old), "manual_discount_price": str(sale)}
    return {"price": str(sale)}


def ozon_category_paths(tree):
    out = {}

    def walk(node, path, inherited_dc=None):
        if isinstance(node, dict):
            name = s(node.get("category_name") or node.get("name") or node.get("type_name"))
            p = path + ([name] if name else [])
            dc = node.get("description_category_id") or node.get("category_id") or inherited_dc
            tid = node.get("type_id")
            if tid is not None and dc is not None:
                clean = [x for x in p if x and norm(x) not in {"все товары", "все категории"}]
                out[(int(dc), int(tid))] = clean
            for key in ("children", "types", "items", "result"):
                val = node.get(key)
                if isinstance(val, list):
                    for x in val:
                        walk(x, p, dc)
                elif isinstance(val, dict):
                    walk(val, p, dc)
        elif isinstance(node, list):
            for x in node:
                walk(x, path, inherited_dc)

    walk(tree, [], None)
    return out


def char_values(row, ids):
    out = []
    for c in row.get("characteristics") or []:
        if s(c.get("characteristic_id")) in ids:
            v = s(c.get("value"))
            if v:
                out.append(v)
            for z in c.get("values") or []:
                if isinstance(z, dict):
                    vv = s(z.get("value"))
                else:
                    vv = s(z)
                if vv:
                    out.append(vv)
    return list(dict.fromkeys(out))


def load_trigger_offer():
    if not TRIGGER.exists():
        return ""
    raw = TRIGGER.read_text(encoding="utf-8").strip()
    if not raw:
        return ""
    try:
        j = json.loads(raw)
        return s(j.get("offer_id"))
    except Exception:
        return s(raw)


def find_or_create_category(kit, categories, ozon_path):
    by_title = defaultdict(list)
    for row in categories:
        title = s(row.get("title") or row.get("name"))
        if title and s(row.get("id")):
            by_title[norm(title)].append(row)

    useful = [x for x in (ozon_path or []) if s(x)]
    if useful:
        leaf = useful[-1]
        exact = by_title.get(norm(leaf), [])
        if len(exact) == 1:
            return s(exact[0].get("id")), "existing_leaf:" + leaf

    root_matches = by_title.get(norm(BRAND), [])
    if len(root_matches) == 1:
        root = root_matches[0]
    elif not root_matches:
        root = kit.create_category(BRAND, None)
        categories.append(root)
        by_title[norm(BRAND)].append(root)
    else:
        root = sorted(root_matches, key=lambda x: s(x.get("id")))[0]

    parent_id = s(root.get("id"))
    path = useful[-2:] if useful else ["Товары"]
    for title in path:
        matches = [
            x for x in categories
            if norm(x.get("title") or x.get("name")) == norm(title)
            and s(x.get("parent_id")) == parent_id
        ]
        if matches:
            row = matches[0]
        else:
            row = kit.create_category(title, parent_id)
            categories.append(row)
        parent_id = s(row.get("id"))
    return parent_id, "created_or_reused_norden_path:" + " > ".join(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offer-id", default="")
    args = ap.parse_args()
    target_offer = s(args.offer_id) or s(os.environ.get("TARGET_OFFER_ID")) or load_trigger_offer()

    report = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target_offer_id": target_offer or None,
        "status": "running",
        "ozon_norden_total": 0,
        "ozon_archived_norden": 0,
        "kit_variants_scanned": 0,
        "matched_existing": 0,
        "created": 0,
        "updated": 0,
        "published": 0,
        "price_updated": 0,
        "images_filled": 0,
        "ambiguous": 0,
        "errors": [],
        "items": [],
    }

    oz = Ozon()
    all_rows = oz.list_visibility("ALL")
    archived_rows = oz.list_visibility("ARCHIVED")
    archived_ids = {int(x.get("product_id") or 0) for x in archived_rows if int(x.get("product_id") or 0)}

    by_id = {}
    for x in all_rows + archived_rows:
        pid = int(x.get("product_id") or 0)
        if pid:
            by_id[pid] = x

    ids = sorted(by_id)
    attrs = oz.attributes(ids)
    attr_by_id = {int(x.get("id") or x.get("product_id") or 0): x for x in attrs}
    norden = [x for x in attrs if is_norden(x)]
    if target_offer:
        norden = [x for x in norden if s(x.get("offer_id")) == target_offer]
        if len(norden) != 1:
            raise RuntimeError(f"Expected exactly one Norden Ozon item {target_offer}, found {len(norden)}")

    norden_ids = [int(x.get("id") or x.get("product_id") or 0) for x in norden]
    infos = oz.info(norden_ids)
    prices = oz.prices(norden_ids)
    info_by_id = {int(x.get("id") or x.get("product_id") or 0): x for x in infos}
    price_by_id = {int(x.get("product_id") or 0): x for x in prices}

    report["ozon_norden_total"] = len(norden)
    report["ozon_archived_norden"] = sum(1 for x in norden_ids if x in archived_ids)

    category_paths = ozon_category_paths(oz.category_tree())

    kit = BRIDGE.KitClient()
    chars = kit.characteristics()
    char_by_title = defaultdict(list)
    for c in chars:
        if s(c.get("id")) and s(c.get("title")):
            char_by_title[MOD.norm_title(c.get("title"))].append(c)

    article_rows = char_by_title.get(MOD.norm_title(ARTICLE_TITLE), [])
    if not article_rows:
        created_char = kit.create_characteristic(ARTICLE_TITLE)
        chars.append(created_char)
        char_by_title[MOD.norm_title(ARTICLE_TITLE)].append(created_char)
        article_rows = [created_char]
    article_ids = {s(x.get("id")) for x in article_rows if s(x.get("id"))}
    article_id = sorted(article_ids)[0]

    by_exact_sku = defaultdict(list)
    by_exact_article = defaultdict(list)
    seen = set()
    for row in kit.scan_all_variants_parallel(workers=10):
        vid = s(row.get("id"))
        if not vid or vid in seen:
            continue
        seen.add(vid)
        report["kit_variants_scanned"] += 1
        sku = s(row.get("sku"))
        if sku:
            by_exact_sku[sku].append(row)
        for av in char_values(row, article_ids):
            by_exact_article[av].append(row)

    categories = kit.categories()
    schema_cache = {}

    for idx, card in enumerate(norden, 1):
        pid = int(card.get("id") or card.get("product_id") or 0)
        offer = s(card.get("offer_id") or by_id.get(pid, {}).get("offer_id"))
        item_report = {
            "offer_id": offer,
            "product_id": pid,
            "ozon_archived": pid in archived_ids,
            "action": None,
            "kit_variant_id": None,
            "price": None,
            "images": 0,
            "error": None,
        }
        try:
            if not offer:
                raise RuntimeError("Ozon offer_id missing")

            matches = {}
            for row in by_exact_sku.get(offer, []) + by_exact_article.get(offer, []):
                if s(row.get("id")):
                    matches[s(row.get("id"))] = row
            if len(matches) > 1:
                report["ambiguous"] += 1
                raise RuntimeError(f"Ambiguous exact KIT article {offer}: variants={sorted(matches)}")

            info = info_by_id.get(pid) or {}
            name = s(card.get("name") or info.get("name") or offer)
            dc = int(card.get("description_category_id") or info.get("description_category_id") or 0)
            tid = int(card.get("type_id") or info.get("type_id") or 0)
            key = (dc, tid)
            if dc and tid and key not in schema_cache:
                schema_cache[key] = oz.schema(dc, tid)
            schema = schema_cache.get(key, {})

            customer_values = POST.customer_attribute_values(card, schema)
            customer_titles = {norm(x["title"]) for x in customer_values}
            pricing = price_patch(price_by_id.get(pid) or {})
            desc = description_from_ozon(card)
            urls = image_urls(card)

            if matches:
                row = next(iter(matches.values()))
                vid = s(row.get("id"))
                report["matched_existing"] += 1
                current = kit.request("GET", f"/v1/variants/{vid}")
                item_report["kit_variant_id"] = vid
                keep = []
                char_meta = {s(x.get("id")): s(x.get("title")) for x in chars}
                for c in current.get("characteristics") or []:
                    title = char_meta.get(s(c.get("characteristic_id")), "")
                    if norm(title) == norm(ARTICLE_TITLE):
                        continue
                    if POST.is_ozon_characteristic_title(title):
                        continue
                    if norm(title) in customer_titles:
                        continue
                    keep.append(c)
                canonical = POST.build_customer_rows(kit, chars, char_by_title, customer_values)
                final_chars = keep + [{"characteristic_id": article_id, "value": offer, "values": [offer]}] + canonical

                patch = {
                    "sku": offer,
                    "name": name,
                    "status": "PUBLISHED",
                    "brand": BRAND,
                    "characteristics": final_chars,
                }
                if desc:
                    patch["description"] = desc
                if pricing:
                    patch["pricing"] = pricing
                    item_report["price"] = pricing
                kit.patch_variant(vid, patch)
                report["updated"] += 1
                if pricing:
                    report["price_updated"] += 1

                current2 = kit.request("GET", f"/v1/variants/{vid}")
                if urls and not (current2.get("media") or []):
                    media = []
                    for u in urls:
                        try:
                            up = kit.upload_image_url(u)
                            fid = s(up.get("id"))
                            if fid:
                                media.append({"type": "IMAGE", "display_sequence": len(media), "image_id": fid})
                        except Exception as exc:
                            report["errors"].append({"offer_id": offer, "stage": "image", "error": str(exc)[:500]})
                    if media:
                        kit.patch_variant(vid, {"media": media})
                        report["images_filled"] += 1
                        item_report["images"] = len(media)
                item_report["action"] = "updated"
            else:
                oz_path = category_paths.get(key, [])
                category_id, category_reason = find_or_create_category(kit, categories, oz_path)
                product = kit.create_product(category_id)
                product_id = s(product.get("id"))
                if not product_id:
                    raise RuntimeError("KIT create product returned no id")
                body = {
                    "sku": offer,
                    "name": name,
                    "status": "PUBLISHED",
                    "product_id": product_id,
                    "brand": BRAND,
                }
                if pricing:
                    body["pricing"] = pricing
                    item_report["price"] = pricing
                created = kit.create_variant(body)
                vid = s(created.get("id"))
                if not vid:
                    raise RuntimeError("KIT create variant returned no id")
                item_report["kit_variant_id"] = vid
                canonical = POST.build_customer_rows(kit, chars, char_by_title, customer_values)
                patch = {
                    "characteristics": [{"characteristic_id": article_id, "value": offer, "values": [offer]}] + canonical
                }
                if desc:
                    patch["description"] = desc
                if urls:
                    media = []
                    for u in urls:
                        try:
                            up = kit.upload_image_url(u)
                            fid = s(up.get("id"))
                            if fid:
                                media.append({"type": "IMAGE", "display_sequence": len(media), "image_id": fid})
                        except Exception as exc:
                            report["errors"].append({"offer_id": offer, "stage": "image", "error": str(exc)[:500]})
                    if media:
                        patch["media"] = media
                        item_report["images"] = len(media)
                        report["images_filled"] += 1
                kit.patch_variant(vid, patch)
                by_exact_sku[offer].append({"id": vid, "sku": offer})
                by_exact_article[offer].append({"id": vid, "sku": offer})
                report["created"] += 1
                if pricing:
                    report["price_updated"] += 1
                item_report["action"] = "created"
                item_report["category"] = category_reason

            verify = kit.request("GET", f"/v1/variants/{item_report['kit_variant_id']}")
            if s(verify.get("status")).upper() != "PUBLISHED":
                raise RuntimeError(f"KIT verify status is {verify.get('status')!r}, expected PUBLISHED")
            if s(verify.get("sku")) != offer:
                raise RuntimeError(f"KIT verify SKU is {verify.get('sku')!r}, expected {offer!r}")
            if s(verify.get("brand")).casefold() != BRAND.casefold():
                raise RuntimeError(f"KIT verify brand is {verify.get('brand')!r}, expected Norden")
            report["published"] += 1

        except Exception as exc:
            item_report["error"] = str(exc)[:1500]
            report["errors"].append({"offer_id": offer, "stage": "item", "error": str(exc)[:1500]})
        report["items"].append(item_report)

        if idx % 25 == 0:
            OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"processed {idx}/{len(norden)} created={report['created']} updated={report['updated']} errors={len(report['errors'])}", flush=True)

    report["status"] = "УСПЕШНО" if not report["errors"] else "ЗАВЕРШЕНО С ОШИБКАМИ"
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "ozon_norden_total": report["ozon_norden_total"],
        "archived": report["ozon_archived_norden"],
        "created": report["created"],
        "updated": report["updated"],
        "published": report["published"],
        "price_updated": report["price_updated"],
        "errors": len(report["errors"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        failure = {
            "status": "ОШИБКА",
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "message": str(exc),
        }
        OUT.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(failure, ensure_ascii=False))
        raise
